# 0009. 部署与常驻运行：进程心跳、R2 备份和服务器初始化

**Date**: 2026-11-02
**Status**: In Progress

## Summary

把 Harvester 从你的 Mac 搬到已经开好的 Lightsail 服务器上，让它一直跑：进程崩溃或服务器重启后自动恢复，dashboard 能看出采集进程是不是还活着。数据库每天加密备份一次，上传到 Cloudflare R2（一个按文件存储的云盘），留 14 天，备份结果显示在 dashboard 上。服务器用仓库里的一个脚本一次初始化好；本地数据库整库迁到服务器，之后本机不再采集。

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Requirements

**范围**：
- 包含：collector 进程级心跳和显示；每日备份、上传、记录和显示；恢复脚本；服务器初始化脚本；手动发布和回滚入口；从本机切到服务器的一次性迁移；部署手册；spec 0004 留下的 4 个线上检查。
- 不包含：卡住时自动重启 collector（你手动重启）；Lightsail 整机快照；从 Mac 远程连服务器数据库和只读账号（等 AI 分析功能再定）；外部告警服务；R2 桶锁；api 进程心跳；显示运行版本和启动时间；数据库迁移的 downgrade。

**User stories**：
- 作为唯一的使用者，我希望采集一直在服务器上跑，Mac 合盖、重启服务器都不用我管，这样不会漏消息。
- 作为唯一的使用者，我希望打开 dashboard 就能看出采集进程是不是还活着、最近一次备份有没有成功，这样出问题时我能马上知道。
- 作为唯一的使用者，我希望服务器坏了也能从备份恢复全部数据，而且备份落到别人手里也读不了。

**Acceptance criteria**：
- **AC-1**：在服务器上执行 `sudo reboot`，没有任何人工操作，`postgres`、`api`、`collector`、`caddy` 全部自动起来；dashboard 能打开，collector 在重启完成后 5 分钟内回到 `online`（受 spec 0005 的登录额度和退避约束）。
- **AC-2**：collector 进程意外退出（在宿主机上对它的进程 `kill -9`）后，Docker 自动把它拉起，账号重新回到 `online`。
- **AC-3**：collector 拿到单实例锁后立刻写一次进程心跳，之后每 30 秒写一次，不管账号处于什么状态（没有账号、`connecting`、`reconnecting`、`login_paused`、`token_invalid`、`banned` 等都写）。单次写入失败只记日志，不退出。
- **AC-4**：进程心跳超过 120 秒没更新，或者已经有账号但从来没有心跳时，侧栏账号指示、设置页账号卡和首页系统状态都显示「采集进程无响应」。没有账号也没有心跳时，首页显示「未运行」，不标红。设置页账号卡和首页系统状态显示「采集进程最近心跳」的相对时间（悬停看完整时间）。collector 恢复后，最多 60 秒内提示消失。原有的两条判断（`online` 但账号心跳超过 120 秒、`connecting` 超过 120 秒）继续有效。
- **AC-5**：服务器每天 UTC 04:00（北京时间 12:00）之后 5 分钟内开始一次备份：`pg_dump` 导出整库，用 age 公钥加密，上传到 R2，文件名形如 `harvester-20261102T040312Z.dump.age`（精确到秒的 UTC 时间）。服务器本机只留最新 2 份。服务器在那个时间关着时，开机后补跑一次。同一时间只会有一个备份在跑，备份和发布不会同时进行。一次备份超过 2 小时会被终止并记为失败，不影响第二天的备份。
- **AC-6**：每次备份都在 `backup_run` 里留一条记录：开始时先写 `running`，上传成功后改成 `succeeded`（带文件大小和文件名），失败时改成 `failed`（带失败的步骤：`dump`、`encrypt`、`upload`）。本机旧文件清理失败不影响记录，只记日志。上一次被打断、超过 6 小时还是 `running` 的记录，下一次备份开始时改成 `failed`，步骤记为 `interrupted`。超过 90 天的记录在每次备份时删除。失败的备份不在 `backups/` 里留下半截文件。
- **AC-7**：首页系统状态显示最近一次备份：完成时间（进行中时显示开始时间和「备份中」）、成功或失败（失败时显示中文步骤：导出、加密、上传、被中断）、加密后的大小（MB，一位小数）。表里至少有一条记录，并且最近一次成功距今超过 48 小时（或从来没成功过）时，图标栏首页入口显示红点，首页这一行标红。表里一条记录都没有时显示「还没有备份」，不告警。
- **AC-8**：服务器上只有 age 公钥。拿到 R2 上的备份文件和服务器上的全部文件，没有密码管理器里的 age 私钥也读不出内容。备份里不包含 `.env`、`TOKEN_ENCRYPTION_KEY` 和 R2 密钥。`backup_run.error` 只存步骤名，不存命令输出。
- **AC-9**：`~/harvester/restore.sh <文件> [私钥文件]` 能把一个备份恢复到服务器的 Postgres 里：接受 `.dump`，也接受 `.dump.age`（同时给出私钥文件），恢复前先停 `api` 和 `collector`，要求输入 `RESTORE` 确认，恢复前清空 `public` schema，所以往已经迁移过的库里恢复旧备份也不会冲突。演练一次：用 Mac 专用的只读 R2 token 下载最新备份，在 Mac 上用私钥解密并恢复到本地 Postgres，`message`、`channel`、`discord_account` 的行数和服务器上一致。
- **AC-10**：在全新的 Ubuntu 24.04 上，把仓库的 `deploy/` 目录复制到 `~/harvester/` 后，以 `sudo ~/harvester/bootstrap.sh ubuntu` 运行一次就完成：Docker Engine 和 Compose 插件（Docker 官方 apt 源）、部署用户加入 `docker` 组、开机自动启动 Docker、2 GB swap、SSH 禁止密码和 root 登录（以实际生效值为准）、只自动安装安全更新且不自动重启机器也不自动重启服务、安装 age 和 rclone、创建备份目录、安装并启用备份定时器。再跑一次不报错、不重复添加任何东西。部署用户没有 SSH 公钥时，脚本不改 SSH 配置并报错退出。
- **AC-11**：从外网扫描服务器的 IPv4 和 IPv6，只有 22、80、443（TCP）和 443（UDP）是开着的；5432 连不上。
- **AC-12**：在 GitHub Actions 页面手动运行 Release，填一个已经发布过的 40 位 git sha 和要更新的服务，就能重新发布或回滚到那个版本，不跑检查、不重新构建镜像；复制到服务器的 compose 文件和脚本来自那个 sha。sha 格式不对、服务不在白名单里、镜像不存在时，运行失败，不登录服务器。
- **AC-13**：切换完成后：本机 collector 已经停止（本机日志里有 `collector.stopping`，之后没有新的登录）；服务器上的 dashboard 用原来的密码和 TOTP 能登录；原有的消息、频道开关和账号都在；collector 用原来的 token 登录并回到 `online`。
- **AC-14**：spec 0004 `verify.md` 里还没勾的 4 个线上步骤全部通过（`/api/docs` 和 `/api/openapi.json` 返回 404；线上 cookie 是 `__Host-harvester_session` 并带 `Secure`、不带 `Domain`；从两个网络登录时安全页显示各自的公网 IP；`SITE_ORIGIN` 规范化）。
- **AC-15**：`deploy/README.md` 写明：第一次上线（含迁移）、日常发布、回滚（默认不带 `migrate`，不支持回到更早的表结构）、发布 collector 会消耗一次登录额度、手动重启卡住的 collector、「已停止」和「无响应」的区别、什么时候需要重启服务器以及怎么查、从备份恢复、每月演练一次恢复、R2 生命周期规则、两个 R2 token 的用途、密钥副本放在哪里。
- **AC-16**：`GET /api/system/status` 只有登录后才能访问，没登录返回 401（统一错误格式）。

## Decision

**Chosen option**: Option 2：服务器上 systemd 定时器跑备份脚本，加密后上传 R2；collector 在主进程里单独写进程心跳；仓库脚本初始化服务器。

数据库在服务器上用 `docker compose exec postgres pg_dump` 导出，age 加密后由 rclone 上传到 R2，结果写回数据库给 dashboard 看；collector 主进程多一个和 Discord 会话无关的心跳任务；`deploy/bootstrap.sh` 和 `deploy/restore.sh` 让开新机器和恢复数据都是一条命令。

**Implementation skills**：`fastapi`（`fastapi/fastapi`，`.claude/skills/fastapi/`）· `postgresql-table-design`（`wshobson/agents`，`.claude/skills/postgresql-table-design/`）· `tanstack-query`（`tanstack-skills/tanstack-skills`，`.claude/skills/tanstack-query/`）· `python-testing-patterns`（`wshobson/agents`，`.claude/skills/python-testing-patterns/`）· `vitest`（`antfu/skills`，`.claude/skills/vitest/`）· `security-review`（`getsentry/skills`，`.claude/skills/security-review/`）· `cloudflare`（`cloudflare/skills`，`.agents/skills/cloudflare/`，R2 桶、token 和生命周期规则）

## Feature design

### 数据模型

一次 Alembic 迁移建两张表，和现有的表都没有外键。

**`collector_heartbeat`**（collector 进程心跳，最多一行）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `boolean` 主键，默认 `true`，约束 `ck_collector_heartbeat_single`：`CHECK (id)` | 是 | 保证只有一行 |
| `last_seen_at` | `timestamptz` | 是 | 数据库 `now()` |

写入：`INSERT ... ON CONFLICT (id) DO UPDATE SET last_seen_at = now()`。不发 `NOTIFY`（和账号心跳一样）。collector 退出时不改这一行。

**`backup_run`**（每次备份一条）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `bigint`，`GENERATED ALWAYS AS IDENTITY`，主键 | 是 | |
| `started_at` | `timestamptz`，默认 `now()` | 是 | |
| `finished_at` | `timestamptz` | 否 | `running` 时为空；写入时用数据库 `now()` |
| `status` | `text`，约束 `ck_backup_run_status`：`IN ('running','succeeded','failed')` | 是 | |
| `size_bytes` | `bigint` | 否 | 加密后文件大小，只有成功时有 |
| `object_key` | `text` | 否 | R2 上的文件名，只有成功时有 |
| `error` | `text`，约束 `ck_backup_run_error`：`IN ('dump','encrypt','upload','interrupted')` | 否 | 只有失败时有 |

- 约束 `ck_backup_run_shape`：`running` 时 `finished_at`、`size_bytes`、`object_key`、`error` 都为空；`succeeded` 时 `finished_at`、`size_bytes`、`object_key` 非空且 `error` 为空；`failed` 时 `finished_at`、`error` 非空，`size_bytes`、`object_key` 为空。
- 索引 `ix_backup_run_started_at`（`started_at DESC`）。表最多几十行，不加别的索引。
- 保留 90 天，由 `backup.sh` 删除。

### 状态

`backup_run.status`：`running` → `succeeded` | `failed`，都由 `backup.sh` 写。`running` 超过 6 小时的由下一次 `backup.sh` 改成 `failed`（`error = 'interrupted'`）。没有别的转换。

### 进程心跳

- 位置：`harvester/collector/main.py` 的 `background` 里加一个任务 `_beat_process`，和 `lock-probe`、`account-poll` 并列，不放进 supervisor。拿到锁、连上库之后先写一次再进入循环，间隔 `PROCESS_HEARTBEAT_INTERVAL = 30.0`。
- 每次写入用一个新的短会话（`get_sessionmaker()`），调用 `harvester.db.system.record_collector_heartbeat(session)`。异常只记 `collector.process_heartbeat_failed`（只记异常类型），不退出；数据库真的断了时由现有的锁探测退出。
- 事件循环被阻塞时心跳自然停止，所以能发现「进程在但卡住」。supervisor 协程逻辑上卡住而事件循环正常时，进程心跳照写，由原有的两条账号规则兜底，所以 `collector_stale` 是两者取或。
- 干净退出（发布、`docker compose stop`）不写「已停止」，120 秒后同样显示「无响应」。手册写明：发布后 1 到 2 分钟内的「无响应」是正常的。
- 纯函数（`harvester.db.system`）：`is_process_stale(last_seen_at: datetime | None, db_now: datetime, *, has_account: bool) -> bool`：`last_seen_at` 为空时返回 `has_account`；否则 `db_now - last_seen_at > COLLECTOR_STALE_AFTER`（复用 `db/account.py` 的 120 秒常量）。
- `DiscordAccountOut.collector_stale` 改为：`is_process_stale(...) or is_collector_stale(account_row)`。读账号时一起读心跳行，用同一个 `db_now`。

### 服务器上的文件

部署用户默认是 Lightsail 自带的 `ubuntu`，目录 `~/harvester/`（即 `/home/ubuntu/harvester/`）：

| 文件 | 来源 | 说明 |
|---|---|---|
| `compose.prod.yml`、`deploy.sh`、`backup.sh`、`restore.sh`、`bootstrap.sh`、`systemd/` | 仓库 `deploy/`；第一次用 `scp -r` 复制，之后每次发布由 Release 复制（`bootstrap.sh` 和 `systemd/` 只在第一次复制） | |
| `.env` | 你手动创建，`chmod 600` | spec 0001 已定 |
| `backup.env` | 你手动创建，`chmod 600` | R2 密钥和 age 公钥，不放进 `.env`，容器读不到 |
| `.image-tag` | `deploy.sh` 写入 | 当前运行的 sha |
| `.ops.lock` | `flock` 自动创建 | 发布和备份互斥 |
| `backups/` | bootstrap 创建，`chmod 700` | 本机缓冲，只留最新 2 份 |

**compose 变量**：`compose.prod.yml` 顶层要求 `IMAGE_TAG` 和 `IMAGE_PREFIX`，任何 compose 命令（包括 `exec`、`stop`）缺了都会报错。`backup.sh` 和 `restore.sh` 开头都这样处理：读 `.image-tag` 得到 `IMAGE_TAG`；文件不存在（还没发布过）时设成占位值 `none`。这两个脚本只对 `postgres` 做 `exec`，或对其他服务做 `stop`，都不拉取镜像，所以占位值是安全的。`IMAGE_PREFIX` 来自 `.env`。

**互斥**：`deploy.sh` 和 `backup.sh` 都用 `flock ~/harvester/.ops.lock` 包住整个过程，后来的等前面的结束（`deploy.sh` 最多等 30 分钟，`backup.sh` 最多等 1 小时，超时就失败退出）。这样 `pg_dump` 持有的锁不会和 `migrate` 的表结构修改互相卡住。

### 备份

`harvester-backup.timer`：`OnCalendar=*-*-* 04:00:00 UTC`，`Persistent=true`（错过就开机补跑），`RandomizedDelaySec=300`。

`harvester-backup.service`：`Type=oneshot`；`User=`、`WorkingDirectory=`、`ExecStart=` 由 bootstrap 用 `getent passwd <部署用户>` 得到的家目录填成绝对路径（单元模板里写占位符 `@USER@`、`@HOME@`，不用 `%h`，因为系统级单元里 `%h` 是 root 的家目录）；`After=docker.service`，`Requires=docker.service`；`TimeoutStartSec=2h`；`Nice=10`，`IOSchedulingClass=idle`。systemd 不会同时启动同一个 oneshot 服务两次。

`backup.sh` 步骤（`set -euo pipefail`；用 `trap` 在任何退出时删除本次的 `.partial` 文件；不开 `set -x`，不打印任何变量值）：
1. 读 `backup.env` 和 `.env` 里的 `POSTGRES_USER`、`POSTGRES_DB`、`IMAGE_PREFIX`；设置 `IMAGE_TAG`（见上）。缺变量直接退出（这时还没写记录，只在 journald 里）。
2. 拿 `flock`。
3. 等数据库就绪：循环 `exec -T postgres pg_isready`，最多 2 分钟（开机补跑时 postgres 可能还在启动）。超时退出，只在 journald 里。
4. 用 `exec -T postgres psql -qtA -v ON_ERROR_STOP=1`：把超过 6 小时的 `running` 改成 `failed/interrupted`；删除 90 天前的记录；插入一条 `running` 并 `RETURNING id`。
5. 文件名 `harvester-$(date -u +%Y%m%dT%H%M%SZ).dump.age`。在 `set +e` 下执行管道 `exec -T postgres pg_dump -Fc ... | age -r "$BACKUP_AGE_RECIPIENT" -o backups/<名字>.partial`，立刻读 `PIPESTATUS`，再 `set -e`。判断：`age` 非零，或 `pg_dump` 的退出码是 141（age 先退出导致管道断开）时记 `encrypt`；否则 `pg_dump` 非零记 `dump`。成功后改名去掉 `.partial`。
6. `upload`：`rclone copyto backups/<名字> r2:$R2_BUCKET/<名字> --s3-no-check-bucket --retries 3`。rclone 远端完全用环境变量配置（`RCLONE_CONFIG_R2_TYPE=s3`、`RCLONE_CONFIG_R2_PROVIDER=Cloudflare`、`RCLONE_CONFIG_R2_ENDPOINT=https://$R2_ACCOUNT_ID.r2.cloudflarestorage.com`、`RCLONE_CONFIG_R2_ACCESS_KEY_ID`、`RCLONE_CONFIG_R2_SECRET_ACCESS_KEY`），不写 rclone 配置文件。
7. 把记录改成 `succeeded`，`finished_at = now()`，写入 `size_bytes`（`stat -c %s`）和 `object_key`。
8. 清理：删除 `backups/` 里除最新 2 份以外的文件。失败只记日志，不改记录。
- 第 5、6 步失败时：把记录改成 `failed`、`finished_at = now()`、对应步骤名，然后退出非零。
- 日志写到 stdout，由 journald 收集（`journalctl -u harvester-backup`）。
- R2 上的过期删除由桶的生命周期规则负责（上传 14 天后删除），脚本不删 R2 上的文件。服务器上的 R2 token 是「对象读写」，作用范围只限这一个桶。

### 恢复

`restore.sh <dump 文件> [age 私钥文件]`：
1. 设置 `IMAGE_TAG`（见上）。文件名以 `.age` 结尾时必须给私钥文件，否则退出。
2. 打印目标数据库名和文件名，要求输入 `RESTORE`，其他输入就退出。
3. `docker compose stop api collector`（服务还没创建过也不报错），`up -d --wait postgres`。
4. 清空：`exec -T postgres psql -v ON_ERROR_STOP=1 -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'`。`vector` 扩展随 `public` 里的对象一起删除，由 dump 里的 `CREATE EXTENSION` 重建。
5. 恢复：`.age` 文件用 `age -d -i <私钥>` 解密后通过管道，否则直接读文件，交给 `exec -T postgres pg_restore --no-owner --no-acl --role="$POSTGRES_USER" --single-transaction --exit-on-error -U ... -d ...`。
6. 结束后不自动启动服务，打印下一步：`IMAGE_TAG=<sha> ./deploy.sh migrate caddy api collector`（`.image-tag` 存在时打印里面的 sha），迁移会补上备份之后新增的表结构。
- 手册提醒：私钥文件用完立刻删除。
- Mac 上的演练（AC-9）对本地 `compose.yml` 执行同样的清空和恢复命令；手册写出完整命令，不另写脚本。

### 服务器初始化（`bootstrap.sh`）

以 `sudo ~/harvester/bootstrap.sh <部署用户>` 运行，参数默认 `ubuntu`。每一步先检查再做：

| 步骤 | 已完成的判断 |
|---|---|
| 1. 安装 Docker Engine 和 Compose 插件（Docker 官方 apt 源），`systemctl enable --now docker` | `docker compose version` 成功 |
| 2. 部署用户加入 `docker` 组 | `id -nG <用户>` 里有 `docker` |
| 3. 建 2 GB `/swapfile`，写 `/etc/fstab`，`vm.swappiness=10` 写进 `/etc/sysctl.d/90-harvester.conf` | `swapon --show` 里有 `/swapfile`；`grep` fstab 已有这一行 |
| 4. SSH：先确认 `<家目录>/.ssh/authorized_keys` 存在且非空，否则报错退出。写 `/etc/ssh/sshd_config.d/00-harvester.conf`（`PasswordAuthentication no`、`KbdInteractiveAuthentication no`、`PermitRootLogin no`），文件名排在 `50-cloud-init.conf` 前面，因为 sshd 取第一次出现的值。`sshd -t` 通过后 `systemctl reload ssh`，再用 `sshd -T` 确认三项实际生效；不通过就删掉这个文件、reload 并退出 | 文件内容与要写的完全一致 |
| 5. 安装 `unattended-upgrades`，只允许安全更新来源，`Unattended-Upgrade::Automatic-Reboot "false"`；写 `/etc/needrestart/conf.d/90-harvester.conf`：`$nrconf{restart} = 'l';`（只列出需要重启的服务，不自动重启，避免更新 libc 或 openssl 后自动重启 `docker.service` 带着所有容器重启）。Docker 的 apt 源不在自动更新范围里 | 配置文件内容一致 |
| 6. `apt install age rclone` | `command -v age rclone` |
| 7. 建 `<家目录>/harvester/backups/`（`700`，属于部署用户） | 目录存在且权限正确 |
| 8. 用 `systemd/` 里的模板替换 `@USER@`、`@HOME@`，写到 `/etc/systemd/system/`，`daemon-reload`，`enable --now harvester-backup.timer` | 生成的内容与现有文件一致时不重写 |

- 防火墙用 Lightsail 控制台的实例防火墙（IPv4 和 IPv6 两套规则都只开 TCP 22、80、443 和 UDP 443），bootstrap 不装 ufw：Docker 发布的端口会绕过 ufw，装了反而让人误以为有保护。
- GHCR 登录、`.env` 和 `backup.env` 含密钥，不在脚本里，由手册写成手动步骤。

### 发布流程改动

- `release.yml` 加 `workflow_dispatch`，两个输入：`sha`（必填）和 `services`（默认 `caddy api`）。
- 手动运行时 `checks`、`plan`、`publish` 用 `if: github.event_name == 'push'` 跳过。`deploy` 加 `if: ${{ !cancelled() && (github.event_name == 'workflow_dispatch' || needs.publish.result == 'success') }}`。
- 手动运行时 `deploy` 先做三件事，任何一件不通过就失败，不登录服务器：
  1. 输入通过 `env` 传给脚本（不直接拼进 `run` 文本），`sha` 必须匹配 `^[0-9a-f]{40}$`，`services` 的每一项必须在 `migrate caddy api collector` 里。
  2. `docker/login-action` 登录 GHCR（job 权限 `packages: read`），`docker manifest inspect` 确认 `<prefix>-backend:<sha>` 和 `<prefix>-caddy:<sha>` 都存在（`migrate` 用的就是 backend 镜像）。
  3. `actions/checkout` 用 `ref: <sha>`，让复制到服务器的 compose 文件和脚本与镜像同一版本。
- 两种触发方式的复制列表都是 `deploy/compose.prod.yml deploy/deploy.sh deploy/backup.sh deploy/restore.sh`，然后 `chmod +x`。
- `deploy.sh` 用 `flock` 包住整个过程（见「互斥」），其他不变。
- 发布 collector（或重启服务器）会断开 Discord 会话，重新登录消耗 1 次登录额度（spec 0005，24 小时 10 次），手册写明。回滚默认只带 `caddy api collector`，不带 `migrate`；回滚到更早的表结构不支持，因为没有 downgrade 约定。

### 一次性迁移（从 Mac 到服务器）

1. 准备：确认本机 `alembic current` 和要发布的 main 最新 sha 的迁移版本一致（本机就在那个 sha 上）。
2. 服务器：`scp -r deploy/ ubuntu@<IP>:~/harvester/`；`sudo ~/harvester/bootstrap.sh ubuntu`；重新登录让 `docker` 组生效；`docker login ghcr.io`（用只有 `read:packages` 的 PAT）；写 `.env`（`TOKEN_ENCRYPTION_KEY` 必须和本机一样；`POSTGRES_PASSWORD` 用新的随机值）；写 `backup.env`；确认 DNS 指向静态 IP；在 Lightsail 控制台设好 IPv4 和 IPv6 防火墙。
3. Mac：停掉本机 collector，确认日志里有 `collector.stopping`；`docker compose exec -T postgres pg_dump -Fc -U harvester -d harvester > harvester-migrate.dump`；`scp` 到服务器。
4. 服务器：`~/harvester/restore.sh harvester-migrate.dump`（还没发布过，`IMAGE_TAG` 用占位值），然后手动运行 Release（sha 用第 1 步那个，服务 `migrate caddy api collector`）。
5. 打开 `https://<域名>`，用原来的密码和 TOTP 登录，看到 collector 回到 `online`。Discord 第一次看到这个机房 IP，可能要求验证；这时账号会进入 `verification_required`，在浏览器里登录小号完成验证后在 dashboard 点重新连接（spec 0005 已有流程）。
6. Mac：本机 `.env` 换成新生成的开发用 `TOKEN_ENCRYPTION_KEY`（原来的密钥只留在服务器 `.env` 和密码管理器里）。这样本机库里的旧 token 解不开，本机 collector 即使被误启动也不能用小号登录。本机的数据卷和 `harvester-migrate.dump` 先留着，等服务器第一次定时备份成功、AC-9 演练通过后，再 `docker compose down -v` 换成空库并删掉 dump 文件。

### API surface

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/system/status` | GET | 无 | `collector: {last_heartbeat_at: datetime \| null, stale: bool, has_account: bool}`，`backup: {last_run: BackupRunOut \| null, last_success_at: datetime \| null, overdue: bool}` | 登录会话（`require_session`） | 401 `unauthenticated`，503 `database_unavailable` |
| `/api/discord/account`（现有） | GET | 无 | `collector_stale` 的计算改为两者取或，字段不变 | 登录会话 | 不变 |

`BackupRunOut`：`started_at`、`finished_at`、`status`、`size_bytes`、`object_key`（方便你在 R2 里找到那个文件）、`error`。不返回服务器路径、桶名或任何密钥。改接口后按 `AGENTS.md` 的命令导出 OpenAPI 并重新生成前端类型。

前端：
- 新 hook `useSystemStatus`（`src/components/system/`），TanStack Query，`refetchInterval: 30_000`，失败时走统一的 `toUiError`。
- 首页 `SystemStatus` 加两行：
  - 「采集进程」：`stale` 时显示「采集进程无响应」（error）；`last_heartbeat_at` 为空且 `has_account` 为假时显示「未运行」（pending）；否则「正常」加最近心跳的相对时间。
  - 「备份」：`last_run` 为空时「还没有备份」（pending）；`running` 时「备份中」加开始时间；`succeeded` 时完成时间和大小；`failed` 时「失败：<步骤中文>」加完成时间。`overdue` 时这一行用 error 色调。步骤中文：`dump` 导出、`encrypt` 加密、`upload` 上传、`interrupted` 被中断。大小 `size_bytes / 1_000_000` 保留一位小数加「MB」。
- 设置页账号卡（`account-panel.tsx`）加一行「采集进程最近心跳：x 分钟前」，悬停看完整时间；没有心跳时显示「还没有心跳」。
- 侧栏的账号指示继续用 `collector_stale`；图标栏的首页入口在 `backup.overdue` 时显示红点，`aria-label` 为「备份超过 2 天没有成功」。

### Value sourcing

| Action | Value produced / displayed | Source |
|---|---|---|
| collector 写心跳 | `last_seen_at` | 数据库 `now()` |
| `/api/system/status` | `collector.last_heartbeat_at` | `collector_heartbeat.last_seen_at`，没有行时为 `null` |
| `/api/system/status` | `collector.has_account` | 存在 `discord_account` 行 |
| `/api/system/status` | `collector.stale` | `is_process_stale(last_seen_at, 数据库 now(), has_account=...)` |
| `/api/discord/account` | `collector_stale` | `is_process_stale(...) or is_collector_stale(account_row)`，同一个 `db_now` |
| `/api/system/status` | `backup.last_run` | `backup_run` 按 `started_at DESC` 第一行 |
| `/api/system/status` | `backup.last_success_at` | `backup_run` 中 `status = 'succeeded'` 的最大 `finished_at` |
| `/api/system/status` | `backup.overdue` | 表里至少一行，并且（`last_success_at` 为空，或 数据库 `now() - last_success_at > 48 小时`）；常量 `BACKUP_OVERDUE_AFTER = timedelta(hours=48)` 在 `harvester.db.system` |
| 首页和设置页 | 相对时间和完整时间 | 前端 `createTimeFormat`，时区取浏览器时区（和现有页面一致） |
| 首页「备份」 | 显示的时间 | `succeeded`、`failed` 用 `finished_at`；`running` 用 `started_at` |
| 首页「备份」 | 大小 | `size_bytes`，前端换算成 MB |
| `backup.sh` | 文件名时间戳 | 服务器 UTC 时间 `date -u +%Y%m%dT%H%M%SZ` |
| `backup.sh` | `size_bytes` | `stat -c %s` 读加密后的文件 |
| `backup.sh` | `finished_at` | 数据库 `now()` |
| `backup.sh`、`restore.sh` | `IMAGE_TAG` | `.image-tag`，不存在时占位值 `none` |
| `backup.sh`、`restore.sh` | 数据库用户、库名、`IMAGE_PREFIX` | `~/harvester/.env` |
| `backup.sh` | R2 地址和密钥、age 公钥 | `~/harvester/backup.env` |
| `restore.sh` | age 私钥 | 命令行参数给出的文件路径（只在恢复时临时放置，用完删除；平时只在密码管理器里） |
| Mac 演练 | R2 读取凭据 | Mac 专用的只读 R2 token，存在密码管理器，不复制服务器的 token |
| Release 手动运行 | sha 和服务列表 | `workflow_dispatch` 输入，经格式和白名单校验 |
| bootstrap | 部署用户名、家目录 | 命令行参数（默认 `ubuntu`），家目录用 `getent passwd` |

### Key invariants

- Postgres 在线上不发布任何端口；只能从 compose 网络内部和 `docker compose exec` 访问。
- 同一时间只有一个地方用小号登录：切换完成后本机的加密密钥和服务器不同，本机解不开库里的 token。
- 服务器上永远没有 age 私钥（除了恢复时临时放置，恢复后删除）。
- 备份脚本、恢复脚本和 bootstrap 都不把密钥写进日志、命令行参数或数据库。
- `collector_stale` 的判断只用数据库时钟，不用 api 或浏览器的本地时间。
- 卡住的 collector 不会被自动重启；所有重启都是你手动发起，或者是进程真正退出后由 Docker 拉起。
- 服务器不会因为自动更新而自己重启，Docker 也不会因为自动更新被重启。
- 发布和备份不会同时进行。

### Security model

- `/api/system/status` 和其他接口一样，只有登录会话能访问；返回的内容不含任何密钥、路径或主机名。
- `backup.env` 只有部署用户能读（`600`），不挂进任何容器。服务器上的 R2 token 只授予备份桶的对象读写；Mac 上另有一个只读 token。
- 备份文件用 age 公钥加密；私钥只在密码管理器里。`.env`（含 `TOKEN_ENCRYPTION_KEY` 和数据库密码）的副本也只在密码管理器里，和备份分开存放，所以拿到备份的人也解不开 token。
- SSH 只允许密钥登录，禁止 root；22 端口对所有来源开放，因为 GitHub Actions 的出口 IP 不固定。
- 手动发布的输入经过格式和白名单校验，不直接拼进远程命令。
- 采集到的消息不属于受监管数据，没有合规要求；风险主要是小号 token 泄露和封号（spec 0005）。

### Configuration required

`~/harvester/backup.env`（新，服务器上手动创建）：
- `R2_ACCOUNT_ID`：Cloudflare 账号 ID，用来拼 R2 地址。
- `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`：只作用于备份桶、权限为对象读写的 R2 API token。
- `R2_BUCKET`：桶名。
- `BACKUP_AGE_RECIPIENT`：age 公钥（`age1...`）。

一次性前置准备：
- Cloudflare：建一个私有 R2 桶，加生命周期规则「上传 14 天后删除」；建两个 API token，一个只限这个桶的对象读写（给服务器），一个只限这个桶的只读（给 Mac）。
- 在 Mac 上 `age-keygen -o harvester-backup.key`，私钥存进密码管理器，公钥填进 `backup.env`，然后删掉本地私钥文件。
- 密码管理器：存 `.env` 全文、age 私钥、Mac 用的只读 R2 token，各一条。
- Lightsail 控制台：实例防火墙 IPv4 和 IPv6 都只开 TCP 22、80、443 和 UDP 443。
- GitHub：`DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY` 三个 secrets（spec 0001 已定）。

不新增应用环境变量。

### Critical test scenarios

- Happy path：服务器上 `sudo reboot`，几分钟后 dashboard 能打开，collector 回到 `online`，首页「采集进程」显示刚刚的心跳，verifies **AC-1**、**AC-3**、**AC-4**
- Happy path：手动 `sudo systemctl start harvester-backup`，R2 上出现新文件，首页「备份」显示刚刚成功和大小；Mac 上用只读 token 下载、用私钥解密并恢复，行数一致，verifies **AC-5**、**AC-6**、**AC-7**、**AC-9**
- Failure case：在 `backup.env` 里写错 R2 密钥再触发备份，记录变成 `failed/upload`，`backups/` 里没有 `.partial`；把最近一次成功的 `finished_at` 改到 49 小时前，图标栏出现红点，verifies **AC-6**、**AC-7**
- Failure case：发布进行中手动触发备份，备份等发布结束后才开始；在已经迁移过的库上恢复一份旧备份，再跑 `migrate` 成功，verifies **AC-5**、**AC-9**
- Failure case：`docker compose pause collector`（模拟卡住），120 秒后三个位置都显示「采集进程无响应」，`unpause` 后 60 秒内消失；账号处于 `token_invalid` 时心跳照常更新，verifies **AC-3**、**AC-4**
- Failure case：宿主机上 `kill -9` collector 的进程，Docker 把它拉起，verifies **AC-2**
- Failure case：手动运行 Release，sha 填 `abc` 或服务填 `postgres`，运行失败且没有登录服务器，verifies **AC-12**
- Auth/permission：不带 cookie 请求 `/api/system/status` 返回 401 `unauthenticated`；从外网对 IPv4 和 IPv6 `nc -z <地址> 5432` 都连不上，verifies **AC-16**、**AC-11**

## Build plan

按 Tracer Bullet：先在本地打通「心跳写入 → 接口 → 界面」这条最细的线，再把整条链路搬上服务器，最后加粗备份和恢复。

1. 打通心跳细线：迁移建 `collector_heartbeat` 和 `backup_run`；`harvester.db.system`（`record_collector_heartbeat`、`is_process_stale`、`get_system_status` 及备份相关读取和 `overdue` 计算）；collector 主进程的 `_beat_process` 任务；`collector_stale` 改成两者取或；`GET /api/system/status`；同步 OpenAPI 和前端类型；`useSystemStatus`，首页「采集进程」行、设置页账号卡心跳行，satisfies **AC-3**、**AC-4**、**AC-16**
2. 上线服务器：`deploy/bootstrap.sh` 和 `deploy/systemd/` 两个单元模板；`deploy/restore.sh`；`deploy.sh` 加 `flock`；`release.yml` 加 `workflow_dispatch`、输入校验、镜像检查和新的复制列表；`deploy/README.md` 的「第一次上线和迁移」「日常发布」「回滚」「手动重启 collector」「重启服务器」几节；按手册完成迁移和第一次发布，satisfies **AC-1**、**AC-2**、**AC-10**、**AC-11**、**AC-12**、**AC-13**
3. 备份：`deploy/backup.sh`（8 步、失败记录、`trap` 清理、`flock`）；首页「备份」行、图标栏首页入口的红点；在服务器上配置 R2 和 `backup.env`，手动触发一次并等一次定时触发，satisfies **AC-5**、**AC-6**、**AC-7**、**AC-8**
4. 恢复演练和收尾：Mac 上用只读 token 从 R2 下载、解密、恢复并核对行数；手册补「从备份恢复」「每月演练」「R2 生命周期和两个 token」「密钥副本」「已停止和无响应的区别」；跑 spec 0004 的 4 个线上步骤；演练通过后清理本机的迁移数据，satisfies **AC-9**、**AC-13**、**AC-14**、**AC-15**
5. 测试：`harvester.db.system` 的纯函数和连真实 PostgreSQL 的集成测试（心跳 upsert、单行约束、`backup_run` 的三个约束、`overdue` 的边界：无记录、只有失败、只有 `running`、刚好 48 小时）；`/api/system/status` 加入路由鉴权遍历；collector 心跳任务在各种账号状态下都写入；前端 `SystemStatus` 各种状态、账号卡和图标栏红点的渲染测试；`bash -n` 检查四个脚本，satisfies **AC-3**、**AC-4**、**AC-6**、**AC-7**、**AC-16**

## Consequences

**Positive**:
- 采集不再依赖 Mac 开着，spec 0007 的睡眠保护在线上用不上，问题自然消失。
- collector 在任何账号状态下挂掉或卡住都能在 dashboard 上看出来，补上 spec 0005 留下的空白。
- 备份离开 AWS，在另一家厂商，加密存放；服务器、AWS 账号或 R2 桶任何一个单独出事，数据都还在。
- 开新服务器、恢复数据都是写好的脚本加手册，不靠记忆。

**Negative / tradeoffs**:
- 没有整机快照：服务器坏了要开新实例、跑 bootstrap、手动放回 `.env` 和 `backup.env`、恢复备份，大约要半小时到一小时，期间不采集。
- 最多丢 24 小时的数据（两次备份之间）。实时消息可以等第 10 项上线后补采，但编辑的中间版本补不回来。
- 卡住的 collector 不会自己恢复，要等你看到后手动重启，夜里卡住就会漏到早上。
- 不开桶锁：服务器被攻破时，攻击者可以用服务器上的 R2 token 删除备份。
- 服务器不自动重启，服务也不自动重启：安全更新要等你手动重启才完全生效，手册写怎么查。
- 备份失败只在 dashboard 上显示，你不打开就不知道，没有邮件或推送。第一次备份进行中或第一次就失败时，红点会立刻出现，这是预期行为。
- 发布和备份互相等待：备份进行中触发发布，发布会晚几分钟开始。
- 回滚只能回到同一表结构下的版本。
- Discord 第一次看到机房 IP 可能要求验证，切换当天可能多一次人工操作。
- 切换期间漏采几分钟，到第 10 项才能补。

**Neutral**:
- 新增 `harvester.db.system`，改动它按发布规则会让 collector 重启一次。
- 新增两张表、一个接口、三个 shell 脚本、两个 systemd 单元模板；`deploy.sh` 只多一层 `flock`。
- Postgres 不对外，以后要从 Mac 连服务器数据库，需要单独设计（见 Follow-up）。
- 要管理两个 R2 token、一把 age 私钥和一份 `.env` 副本。

## Follow-up

- [ ] AI 分析功能设计时，再决定从 Mac 连服务器数据库的方式（SSH 隧道、Tailscale 等）和只读账号。
- [ ] Lightsail 是突发型 CPU（spec 0001），上线一段时间后看一下 CPU 积分；历史采集（第 10 项）前再看一次。
- [ ] 附件保存（第 12 项）上线后，备份会变大很多：附件文件不进 `pg_dump`，要单独决定附件怎么备份，以及 R2 免费额度够不够。
- [ ] 考虑在 CI 里加 `shellcheck`，检查 `deploy/` 下的脚本。
- [ ] 给 `AGENTS.md`（由 `/sync` 写）的备忘：`## Agent skills` 加上 `cloudflare`（`cloudflare/skills`，`.agents/skills/cloudflare/`，R2 备份桶）；`Declined:` 记上 `bagelhole/devops-security-agent-skills@systemd-services`、`mikr13/secure-server-setup-skills@ssh-hardening`，以及 rclone、age、pg_dump 备份（没有可信的 skill）和 Cloudflare MCP（没有确认过的官方 R2 服务器）。
- [ ] 给 `AGENTS.md` 的备忘：`## Commands` 加上 `sudo ~/harvester/bootstrap.sh`、`~/harvester/restore.sh`、`sudo systemctl start harvester-backup`；部署手册的位置。
- [ ] 以后如果觉得「只在 dashboard 显示」不够，可以给备份和心跳接一个外部的「定时报到」监控服务（比如 Healthchecks.io），漏报就发邮件。
