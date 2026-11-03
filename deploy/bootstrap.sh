#!/usr/bin/env bash
# 一次性初始化一台全新的 Ubuntu 24.04（spec 0009 AC-10）：
#
#   scp -r deploy/ ubuntu@<IP>:~/harvester/
#   sudo ~/harvester/bootstrap.sh ubuntu
#
# 每一步先检查再做，可以放心重复运行。GHCR 登录、.env、backup.env 含密钥，由手册写成手动步骤。
# 防火墙在 Lightsail 控制台设置，这里不装 ufw：Docker 发布的端口会绕过 ufw。
set -euo pipefail

DEPLOY_USER=${1:-ubuntu}
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

log() { echo "bootstrap: $*"; }
die() { echo "bootstrap: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo"
entry=$(getent passwd "$DEPLOY_USER") || die "no such user: ${DEPLOY_USER}"
DEPLOY_HOME=$(cut -d: -f6 <<<"$entry")
[ -d "$DEPLOY_HOME" ] || die "home directory ${DEPLOY_HOME} does not exist"

export DEBIAN_FRONTEND=noninteractive
apt_updated=false
apt_install() {
  if ! $apt_updated; then
    apt-get update -q
    apt_updated=true
  fi
  apt-get install -y -q "$@"
}

# 内容不同才写；写了返回 0，没变返回 1。
write_if_changed() {
  local path=$1 content=$2 mode=${3:-644}
  if [ -f "$path" ] && [ "$(cat "$path")" = "$content" ]; then
    return 1
  fi
  install -d "$(dirname "$path")"
  printf '%s\n' "$content" > "$path"
  chmod "$mode" "$path"
  return 0
}

# 1. Docker Engine 和 Compose 插件（Docker 官方 apt 源），开机自动启动。
if docker compose version >/dev/null 2>&1; then
  log "docker: already installed"
else
  log "docker: installing from the official apt repository"
  apt_install ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091  # 只在服务器上存在。
  codename=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt_updated=false
  apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker >/dev/null

# 2. 部署用户加入 docker 组（重新登录后生效）。
if id -nG "$DEPLOY_USER" | tr ' ' '\n' | grep -qx docker; then
  log "docker group: ${DEPLOY_USER} already a member"
else
  usermod -aG docker "$DEPLOY_USER"
  log "docker group: added ${DEPLOY_USER} (log in again to use it)"
fi

# 3. 2 GB swap。
if swapon --show=NAME --noheadings | grep -qx /swapfile; then
  log "swap: /swapfile already active"
else
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
  log "swap: /swapfile enabled"
fi
if ! grep -qE '^/swapfile[[:space:]]' /etc/fstab; then
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
if write_if_changed /etc/sysctl.d/90-harvester.conf 'vm.swappiness=10'; then
  sysctl -q -p /etc/sysctl.d/90-harvester.conf
fi

# 4. SSH 只允许密钥登录、禁止 root。sshd 取第一次出现的值，所以文件名排在 50-cloud-init.conf 前面。
keys="${DEPLOY_HOME}/.ssh/authorized_keys"
[ -s "$keys" ] || die "${keys} is missing or empty; add your SSH public key first (SSH config left unchanged)"
sshd_conf=/etc/ssh/sshd_config.d/00-harvester.conf
sshd_content='# Written by Harvester bootstrap.sh (spec 0009).
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no'
if write_if_changed "$sshd_conf" "$sshd_content"; then
  if ! sshd -t; then
    rm -f "$sshd_conf"
    die "sshd -t rejected the new config; removed it"
  fi
  systemctl try-reload-or-restart ssh
  log "ssh: hardened config written"
fi
effective=$(sshd -T 2>/dev/null)
for setting in "passwordauthentication no" "kbdinteractiveauthentication no" "permitrootlogin no"; do
  if ! grep -qx "$setting" <<<"$effective"; then
    rm -f "$sshd_conf"
    systemctl try-reload-or-restart ssh
    die "sshd does not report '${setting}'; removed ${sshd_conf}"
  fi
done
log "ssh: password and root login are off"

# 5. 只自动安装安全更新；不自动重启机器，也不自动重启服务（包括 docker.service）。
if ! dpkg -s unattended-upgrades >/dev/null 2>&1; then
  apt_install unattended-upgrades
fi
write_if_changed /etc/apt/apt.conf.d/21harvester-auto-upgrades 'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";' || true
# #clear 先清掉默认列表，再只放回安全来源；Docker 的 apt 源不在里面。
# shellcheck disable=SC2016  # ${distro_id} 是给 apt 的字面量，不是 shell 变量。
write_if_changed /etc/apt/apt.conf.d/52harvester-unattended-upgrades '#clear Unattended-Upgrade::Allowed-Origins;
Unattended-Upgrade::Allowed-Origins {
  "${distro_id}:${distro_codename}-security";
  "${distro_id}ESMApps:${distro_codename}-apps-security";
  "${distro_id}ESM:${distro_codename}-infra-security";
};
Unattended-Upgrade::Automatic-Reboot "false";' || true
write_if_changed /etc/needrestart/conf.d/90-harvester.conf "\$nrconf{restart} = 'l';" || true
log "updates: security only, no automatic reboot or service restart"

# 6. age 和 rclone。
if command -v age >/dev/null && command -v rclone >/dev/null; then
  log "age and rclone: already installed"
else
  apt_install age rclone
fi

# 7. 备份目录，只有部署用户能进。
backups="${DEPLOY_HOME}/harvester/backups"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$backups"
chmod 700 "$backups"

# 8. 备份定时器：用模板替换 @USER@ 和 @HOME@，写到 /etc/systemd/system/。
units_changed=false
for unit in harvester-backup.service harvester-backup.timer; do
  rendered=$(sed -e "s|@USER@|${DEPLOY_USER}|g" -e "s|@HOME@|${DEPLOY_HOME}|g" \
    "${SCRIPT_DIR}/systemd/${unit}")
  if write_if_changed "/etc/systemd/system/${unit}" "$rendered"; then
    units_changed=true
  fi
done
if $units_changed; then
  systemctl daemon-reload
fi
systemctl enable --now harvester-backup.timer >/dev/null 2>&1
log "backup timer: $(systemctl show -p NextElapseUSecRealtime --value harvester-backup.timer)"

log "done. Next steps are in ${SCRIPT_DIR}/README.md (GHCR login, .env, backup.env)."
