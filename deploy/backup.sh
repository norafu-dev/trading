#!/usr/bin/env bash
# 每日备份（spec 0009），由 harvester-backup.service 以部署用户运行，也可以手动：
#
#   sudo systemctl start harvester-backup     # 推荐：日志进 journalctl -u harvester-backup
#   ~/harvester/backup.sh                     # 直接跑也行
#
# pg_dump 整库 → age 公钥加密 → rclone 上传 R2 → 结果写进 backup_run 表。
# 不开 set -x，也从不打印任何变量值：backup.env 里有 R2 密钥。
set -euo pipefail

cd "$(dirname "$0")"

log() { echo "backup: $*"; }

# 从 KEY=VALUE 文件里取一个值（取最后一次出现的，去掉两边的引号）。不 source，免得执行文件内容。
env_value() {
  local file=$1 key=$2 line
  line=$(grep -E "^${key}=" "$file" | tail -n 1 || true)
  line=${line#*=}
  line=${line%\"}; line=${line#\"}
  line=${line%\'}; line=${line#\'}
  printf '%s' "$line"
}

require() {
  local name=$1
  if [ -z "${!name:-}" ]; then
    log "missing ${name}" >&2
    exit 1
  fi
}

# 1. 读配置。这时还没写记录，缺东西只会出现在 journald 里。
for file in .env backup.env; do
  if [ ! -r "$file" ]; then
    log "cannot read ${file}" >&2
    exit 1
  fi
done
POSTGRES_USER=$(env_value .env POSTGRES_USER)
POSTGRES_DB=$(env_value .env POSTGRES_DB)
IMAGE_PREFIX=$(env_value .env IMAGE_PREFIX)
R2_ACCOUNT_ID=$(env_value backup.env R2_ACCOUNT_ID)
R2_ACCESS_KEY_ID=$(env_value backup.env R2_ACCESS_KEY_ID)
R2_SECRET_ACCESS_KEY=$(env_value backup.env R2_SECRET_ACCESS_KEY)
R2_BUCKET=$(env_value backup.env R2_BUCKET)
BACKUP_AGE_RECIPIENT=$(env_value backup.env BACKUP_AGE_RECIPIENT)
for name in POSTGRES_USER POSTGRES_DB IMAGE_PREFIX R2_ACCOUNT_ID R2_ACCESS_KEY_ID \
  R2_SECRET_ACCESS_KEY R2_BUCKET BACKUP_AGE_RECIPIENT; do
  require "$name"
done

# compose.prod.yml 要求 IMAGE_TAG；这里只 exec 进 postgres，不拉镜像，所以还没发布过时用占位值。
IMAGE_TAG=none
if [ -r .image-tag ]; then
  IMAGE_TAG=$(env_value .image-tag IMAGE_TAG)
fi
export IMAGE_TAG IMAGE_PREFIX

# rclone 远端完全用环境变量配置，不写配置文件。
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"

compose=(docker compose -f compose.prod.yml --env-file .env)
psql_run() {
  "${compose[@]}" exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qtAX -v ON_ERROR_STOP=1 "$@"
}

# 2. 和发布互斥：发布进行中就等，最多 1 小时。
exec 9>.ops.lock
if ! flock -w 3600 9; then
  log "timed out waiting for .ops.lock (a deploy is still running)" >&2
  exit 1
fi

# 3. 等数据库就绪（开机补跑时 postgres 可能还在启动），最多 2 分钟。
ready=false
for _ in $(seq 1 60); do
  if "${compose[@]}" exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 2
done
if ! $ready; then
  log "database not ready after 2 minutes" >&2
  exit 1
fi

# 4. 收尾上一次被打断的记录、删掉 90 天前的记录、写一条 running。
run_id=$(psql_run <<'SQL'
BEGIN;
UPDATE backup_run SET status = 'failed', finished_at = now(), error = 'interrupted'
  WHERE status = 'running' AND started_at < now() - interval '6 hours';
DELETE FROM backup_run WHERE started_at < now() - interval '90 days';
INSERT INTO backup_run (status) VALUES ('running') RETURNING id;
COMMIT;
SQL
)
run_id=$(printf '%s' "$run_id" | tr -dc '0-9')
if [ -z "$run_id" ]; then
  log "could not create a backup_run row" >&2
  exit 1
fi
log "started run ${run_id}"

fail() {
  local step=$1
  # 只存步骤名，不存命令输出（AC-8）。
  psql_run -c "UPDATE backup_run SET status = 'failed', finished_at = now(), error = '${step}'
    WHERE id = ${run_id}" || log "could not record the failure" >&2
  log "run ${run_id} failed at ${step}" >&2
  exit 1
}

# 5. 导出并加密。.partial 在任何退出时都删掉，失败不会留下半截文件。
name="harvester-$(date -u +%Y%m%dT%H%M%SZ).dump.age"
mkdir -p backups
partial="backups/${name}.partial"
trap 'rm -f "$partial"' EXIT

set +e
"${compose[@]}" exec -T postgres pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  | age -r "$BACKUP_AGE_RECIPIENT" -o "$partial"
statuses=("${PIPESTATUS[@]}")
set -e
dump_status=${statuses[0]}
age_status=${statuses[1]}
# age 先退出会让 pg_dump 收到 SIGPIPE（141），那是加密的错。
if [ "$age_status" -ne 0 ] || [ "$dump_status" -eq 141 ]; then
  fail encrypt
elif [ "$dump_status" -ne 0 ]; then
  fail dump
fi
mv "$partial" "backups/${name}"
log "dumped and encrypted ${name}"

# 6. 上传。
if ! rclone copyto "backups/${name}" "r2:${R2_BUCKET}/${name}" \
  --s3-no-check-bucket --retries 3; then
  fail upload
fi

# 7. 记成功。
size=$(stat -c %s "backups/${name}")
psql_run -c "UPDATE backup_run SET status = 'succeeded', finished_at = now(),
  size_bytes = ${size}, object_key = '${name}' WHERE id = ${run_id}"
log "run ${run_id} succeeded (${size} bytes)"

# 8. 本机只留最新 2 份；清理失败不影响记录。
if ! find backups -maxdepth 1 -name 'harvester-*.dump.age' -printf '%T@ %p\n' \
  | sort -rn | tail -n +3 | cut -d' ' -f2- | xargs -r rm -f; then
  log "could not prune old local backups" >&2
fi
