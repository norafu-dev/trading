# Verify: 部署与常驻运行 · spec 0009 · updated 2026-11-02
_Steps derived from spec 0009 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._
_「本地」可以在 Mac 上跑；「线上」要等按 `deploy/README.md` 完成第一次上线之后。_

## UI / manual（本地）
- [x] 本地起 api、collector、前端，登录后首页「采集进程」显示「正常」和「最近心跳 x 秒前」，悬停显示完整时间 → AC-3, AC-4
- [x] 设置页账号卡显示「采集进程最近心跳：x 秒前」，悬停显示完整时间 → AC-4
- [x] Ctrl+C 停掉 collector，等 2 分钟以上：首页「采集进程」、设置页账号卡、侧栏账号指示都显示「采集进程无响应」；重新启动 collector，60 秒内提示消失 → AC-4
- [x] 删掉账号（或用空库），清空 `collector_heartbeat`，不启动 collector：首页「采集进程」显示「未运行」，不标红 → AC-4
- [x] 把账号改成 `token_invalid`，collector 照常运行：`collector_heartbeat.last_seen_at` 每 30 秒前进一次 → AC-3
- [x] `backup_run` 为空时首页「备份」显示「还没有备份」，图标栏首页入口没有红点 → AC-7
- [x] 手动插一条 `succeeded`（`size_bytes = 12345678`）：首页显示「成功」、完成时间和「12.3 MB」 → AC-7
- [x] 把这条的 `finished_at` 改到 49 小时前：首页「备份」行标红，图标栏首页入口出现红点，读屏名称是「Harvester 首页，备份超过 2 天没有成功」 → AC-7
- [x] 插一条 `failed`，`error` 依次为 `dump`、`encrypt`、`upload`、`interrupted`：显示「失败：导出 / 加密 / 上传 / 被中断」 → AC-7
- [x] 插一条 `running`：显示「备份中」和开始时间 → AC-7

## UI / manual（线上）
- [ ] 服务器上 `sudo reboot`，不做任何操作：几分钟后 dashboard 能打开，collector 5 分钟内回到「在线」，首页「采集进程」显示刚刚的心跳 → AC-1, AC-3
- [ ] 宿主机上 `sudo kill -9 <collector 的 python 进程 pid>`：Docker 自动拉起，账号回到「在线」 → AC-2
- [ ] `docker compose pause collector`，120 秒后三个位置显示「采集进程无响应」；`unpause` 后 60 秒内消失 → AC-4
- [ ] 切换完成：本机日志有 `collector.stopping` 且之后没有新登录；线上用原密码和 TOTP 能登录；原有消息、频道开关、账号都在；collector 回到「在线」 → AC-13
- [ ] spec 0004 `verify.md` 里没勾的 4 个线上步骤全部通过 → AC-14

## Commands（线上）
- [ ] 全新 Ubuntu 24.04 上 `sudo ~/harvester/bootstrap.sh ubuntu` 跑完不报错；再跑一次也不报错，`/etc/fstab` 里 `/swapfile` 只有一行 → AC-10
- [ ] `sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin) '` 三项都是 `no` → AC-10
- [ ] 临时把 `authorized_keys` 挪走再跑 bootstrap：报错退出，`/etc/ssh/sshd_config.d/` 没有变化（之后挪回来） → AC-10
- [ ] `swapon --show` 有 2G 的 `/swapfile`；`systemctl is-enabled docker` 是 `enabled`；`systemctl list-timers harvester-backup.timer` 有下次运行时间 → AC-10
- [ ] `apt-config dump | grep -A5 Allowed-Origins` 只有安全来源；`Automatic-Reboot` 为 `false`；`/etc/needrestart/conf.d/90-harvester.conf` 是 `'l'` → AC-10
- [ ] 从外网 `nmap -Pn -p- <IPv4>` 和 `nmap -6 -Pn -p- <IPv6>` 只有 22、80、443 开着；`nmap -sU -p 443` 开着；`nc -z -w3 <地址> 5432` 两个地址都连不上 → AC-11
- [ ] 手动运行 Release，sha 填一个已发布的 sha，服务填 `caddy api`：运行成功，服务器 `.image-tag` 变成这个 sha → AC-12
- [ ] 手动运行 Release，sha 填 `abc`：运行失败，日志里没有 SSH 步骤 → AC-12
- [ ] 手动运行 Release，服务填 `postgres`：运行失败，没有登录服务器 → AC-12
- [ ] 手动运行 Release，sha 填一个格式正确但没构建过镜像的 sha：在「Check images exist」失败 → AC-12
- [ ] `sudo systemctl start harvester-backup`：R2 上出现 `harvester-<UTC 时间>.dump.age`，`backup_run` 最新一条是 `succeeded`，带大小和文件名；`backups/` 里最多 2 份，没有 `.partial` → AC-5, AC-6
- [ ] 第二天 UTC 04:00 到 04:05 之间 `journalctl -u harvester-backup` 有一次自动运行 → AC-5
- [ ] `backup.env` 里写错 R2 密钥再触发备份：最新记录是 `failed`、`error = upload`；`backups/` 没有 `.partial` → AC-6
- [ ] `backup.env` 里写错 age 公钥再触发：记录是 `failed`、`error = encrypt` → AC-6
- [ ] 手动插一条 7 小时前开始的 `running`，再触发备份：那条变成 `failed`、`error = interrupted` → AC-6
- [ ] 一次部署进行中触发备份：`journalctl` 显示备份等部署结束后才开始 → AC-5
- [ ] `backup_run.error` 只有步骤名；`journalctl -u harvester-backup` 里没有任何密钥或 R2 地址以外的凭据 → AC-8
- [ ] 下载 R2 上的文件，`head -c 100` 只看到 age 头；服务器上 `grep -r AGE-SECRET-KEY ~` 找不到私钥 → AC-8
- [ ] 在已经迁移过的库上 `restore.sh` 一份旧备份（`.dump.age` 加私钥），然后 `deploy.sh migrate caddy api collector` 成功 → AC-9
- [ ] `restore.sh` 输入不是 `RESTORE` 时退出，数据库没有变化；`.age` 文件不给私钥时直接报错 → AC-9
- [ ] Mac 上用只读 token 下载最新备份、解密、恢复到本地，`message`、`channel`、`discord_account` 行数和服务器上一致 → AC-9
- [ ] 不带 cookie `curl -i https://<域名>/api/system/status` 返回 401，`error.code` 是 `unauthenticated` → AC-16
- [ ] `deploy/README.md` 写明了 AC-15 列出的每一项 → AC-15

## Value sourcing
- [ ] 心跳时间来自数据库：把 Mac 时钟调快 10 分钟，首页「采集进程」仍然是「正常」 → `last_seen_at` 用数据库 `now()`
- [ ] `has_account` 来自 `discord_account` 行：同样没有心跳，有账号显示「无响应」、没账号显示「未运行」
- [ ] `collector_stale` 两者取或：心跳新鲜但账号 `connecting` 超过 120 秒，仍显示无响应；账号 `token_invalid` 但心跳超过 120 秒，也显示无响应
- [x] `last_run` 按 `started_at` 取最新：先插一条早开始晚结束的成功，再插一条晚开始的失败，首页显示失败
- [ ] `last_success_at` 是成功记录里最大的 `finished_at`：最近一次失败不影响「最近一次成功」，红点只看成功时间
- [x] `overdue` 边界：成功在 47 小时 59 分前不告警，48 小时 1 分前告警；表为空不告警
- [ ] 首页「备份」显示的时间：成功和失败用 `finished_at`，进行中用 `started_at`
- [x] 大小换算：`size_bytes = 1_049_999` 显示 `1.0 MB`，`12_345_678` 显示 `12.3 MB`（除以 1_000_000，不是 1_048_576）
- [ ] 相对和完整时间用浏览器时区：把浏览器时区改成 UTC，完整时间跟着变
- [ ] 备份文件名的时间戳是 UTC：服务器时区设成 Asia/Shanghai 时，文件名仍是 UTC 时间
- [ ] `size_bytes` 是加密后文件的大小：等于 `stat -c %s backups/<文件>`
- [ ] `IMAGE_TAG` 来自 `.image-tag`：删掉 `.image-tag` 后 `backup.sh` 和 `restore.sh` 仍能运行（占位值 `none`）
- [ ] 数据库用户、库名、`IMAGE_PREFIX` 来自 `.env`；R2 和 age 公钥来自 `backup.env`：删掉 `backup.env` 里的 `R2_BUCKET`，备份在写记录前就退出，`backup_run` 没有新行
- [ ] Release 手动输入：sha 和服务只来自 `workflow_dispatch` 输入并经过校验
- [ ] bootstrap 的家目录来自 `getent passwd`：用一个家目录不在 `/home` 下的用户跑，生成的单元文件路径正确

## Acceptance-criteria coverage
- AC-1 线上 reboot · AC-2 kill -9 · AC-3 本地心跳三项、线上 reboot · AC-4 本地四项、线上 pause、Value sourcing 前三项
- AC-5 手动和定时备份、互斥 · AC-6 成功、失败、interrupted · AC-7 本地备份行五项、overdue 边界 · AC-8 日志和文件检查
- AC-9 restore 三项和 Mac 演练 · AC-10 bootstrap 五项 · AC-11 端口扫描 · AC-12 Release 四项 · AC-13 切换检查
- AC-14 spec 0004 线上步骤 · AC-15 手册 · AC-16 未登录 401
