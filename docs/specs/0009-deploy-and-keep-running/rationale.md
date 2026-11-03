# 0009. 部署与常驻运行：决策记录

## Context

Harvester 现在跑在你的 Mac 上。Mac 合盖就停止采集（spec 0007 专门为此加了睡眠保护），漏掉的实时消息要等第 10 项的历史补采才能找回，编辑的中间版本永远找不回来。spec 0001 已经定了线上形态：一台 Lightsail 东京实例（2 vCPU / 4 GB），Docker Compose 跑全部服务并设 `restart: unless-stopped`，GitHub Actions 推镜像到 GHCR，`deploy.sh` 按改动路径只更新受影响的服务，按 git sha 回滚。这些文件已经写好，实例和域名也已经开好，但还从来没在服务器上跑过。

spec 0001 把三件事明确留给了第 9 项：进程级心跳、备份、服务器初始化。spec 0005 也留下一个空白：collector 只在账号 `online` 时写心跳，账号处于 `reconnecting` 或停止状态时进程挂了，dashboard 看不出来。

这里的约束：这是你一个人用的工具，没有值班的人，也没有人会一直盯着；Discord 小号每次重新登录都消耗登录额度（24 小时 10 次），频繁重启会增加封号风险；服务器上存着加密的 token，解密密钥在 `.env` 里；数据库里是你采集的全部消息，没有托管数据库的自动备份。不做这一项的后果是：采集只能在 Mac 醒着时进行，服务器一旦损坏数据全部丢失，采集进程挂了也没人知道。

本机现有的数据库（消息、频道开关、账号、dashboard 登录凭据）要整库搬过去，搬完后本机不再采集。以后的 AI 分析可能要从 Mac 连服务器数据库，这次只保证数据库端口不对外，具体方式等那时再定。

## Options considered

### Option 1：最少改动，只靠 Docker 和 Lightsail 快照

不写进程心跳，dashboard 继续用现有的账号心跳；备份只打开 Lightsail 每日自动快照；服务器初始化写成手册，一条条手动执行。

**Pros**：
- 几乎不用写代码，今天就能上线。
- 快照恢复整机，`.env`、证书、配置全部回来。

**Cons**：
- 快照和服务器在同一个 AWS 账号里，账号出事就全没了；快照不能单独取回数据库，误删一张表也只能整机回滚。
- collector 在非 `online` 状态挂掉仍然看不出来。
- 手册步骤容易漏，换机器时只能靠记忆。

### Option 2：systemd 定时备份到 R2，collector 主进程写进程心跳，仓库脚本初始化（选定）

服务器上 systemd 定时器每天运行仓库里的 `backup.sh`：用容器里的 `pg_dump` 导出，age 公钥加密，rclone 上传到 Cloudflare R2，结果写回 `backup_run` 表给 dashboard 显示。collector 主进程加一个独立于 Discord 会话的心跳任务，写单行表。`bootstrap.sh` 和 `restore.sh` 把开新机器和恢复数据都变成一条命令。

**Pros**：
- 备份在另一家厂商，加密存放，和服务器、AWS 账号都隔开；10 GB 以内免费，下载不收流量费。
- `pg_dump` 用容器里的版本，永远和数据库一致；systemd 的 `Persistent=true` 让关机错过的备份开机补跑，日志在 journald 里好查。
- 进程心跳覆盖所有账号状态；备份失败会出现在 dashboard 上，不会悄悄坏几周。
- 脚本能重复运行，换机器、恢复都可以按同样的步骤再走一遍。

**Cons**：
- 要写和维护三个 shell 脚本、两个 systemd 单元、一个新接口和两张表。
- 要管理三份额外的秘密：R2 token、age 私钥、`.env` 副本。
- 服务器坏了恢复要半小时以上，比整机快照慢。

### Option 3：Compose 里加一个 backup 容器，心跳由 Docker healthcheck 加 autoheal 管

自建一个装好 `pg_dump`、age、rclone 的镜像，作为 compose 服务循环等到点执行；collector 加 Docker healthcheck，由 autoheal 容器重启不健康的 collector。

**Pros**：
- 所有东西都在 Docker 里，服务器上除了 Docker 什么都不用装。
- 卡住的 collector 能自动恢复。

**Cons**：
- 多一个要自己构建和更新的镜像，`pg_dump` 的大版本要手动和数据库对齐。
- 容器里的循环在容器重启时会丢掉计划，错过的备份不会补跑，除非自己写补跑逻辑。
- 自动重启 collector：判断失误（比如数据库短暂卡顿）会导致反复重新登录，消耗登录额度，增加封号风险。
- autoheal 需要挂载 Docker socket，等于给它整台机器的 root 权限。

### Option 4：备份拉到 Mac

服务器每天导出，Mac 用 launchd 定时 rsync 拉回来，不用任何云存储。

**Pros**：
- 不花钱，服务器上不存云密钥。
- 备份在你自己手里。

**Cons**：
- Mac 合盖、出门、坏掉时就没有异地备份；Mac 和备份一起丢的风险是真实存在的。
- 失败提醒只能放在 Mac 上，服务器和 dashboard 都看不到。

## Rationale

核心约束是「一个人用、没人盯着、账号重登有代价」。所以选择的标准是：出错能被看见，恢复有脚本可依，自动化不能放大封号风险。

Option 2 满足前两条：备份结果和进程心跳都进数据库，打开 dashboard 就能看见；`bootstrap.sh` 和 `restore.sh` 让恢复不靠记忆。第三条决定了不选 Option 3 的自动重启：collector 卡住是少见情况，而误判导致的反复登录是这个产品最怕的事（spec 0005 为登录额度专门做了一整套保护）。你确认了「只显示，手动重启」，代价是夜里卡住要到早上才处理。

备份去处一开始倾向拉到 Mac（Option 4），后来改成 R2：Mac 不能保证每天醒着，而 R2 和 AWS 是不同厂商，任一边账号出问题都不会同时丢失服务器和备份。消息是明文，所以上传前用 age 加密；服务器只放公钥，即使服务器被攻破，攻击者也读不了已有的备份。你选择不开桶锁、留 14 天，接受了「服务器被攻破时备份可能被删」的风险，换来更简单的配置。不开 Lightsail 自动快照意味着恢复要重新跑 bootstrap，这是换取更低月费和更少重复备份的有意取舍；`.env` 副本放密码管理器正是为了让这条恢复路径走得通。

进程心跳放在 collector 主进程的独立任务里，而不是 supervisor 里，因为要回答的问题是「进程和事件循环还活着吗」，它必须和 Discord 会话的状态无关。supervisor 逻辑卡住而事件循环正常的情况，进程心跳发现不了，所以保留原有的两条账号规则，`collector_stale` 取两者之或。单行表比给 `discord_account` 加列更合适：没有账号时也要有心跳，而且以后 analyzer 等进程可以照着加。

服务器初始化用仓库里的幂等脚本，而不是手册，因为没有整机快照，换机器是真实会发生的恢复路径。防火墙放在 Lightsail 控制台而不用 ufw，是因为 Docker 发布的端口会绕过 ufw；装一个不起作用的防火墙比不装更危险。安全更新自动装但不自动重启，避免服务器在你不知情时重启导致重新登录和漏采。

几个由我决定的细节：
- 备份时间 UTC 04:00（北京时间 12:00）是你选的，白天出错当天就能看到；不选深夜是因为深夜你看不到失败。
- `overdue` 阈值 48 小时：允许一次失败加补跑，不会因为一次网络抖动就告警。表里没有记录时不告警，避免本地开发一直显示警告；代价是服务器上定时器根本没装时不会提醒，由 AC-5 的验收步骤兜住。备选是加一个只在线上打开的配置开关，多一个配置，不值得。
- R2 上的过期删除交给桶的生命周期规则，不在脚本里删。这样保留规则只在一个地方定义，脚本也不用列出和删除云上的文件。
- `restore.sh` 先停 `api` 和 `collector`，防止恢复期间有写入，也防止 collector 带着恢复前的状态登录。恢复前清空 `public` schema，而不是靠 `pg_restore --clean`：后者删不掉比备份更新的表，恢复后再跑 `migrate` 会冲突。
- 切换时 `POSTGRES_PASSWORD` 用新值、`TOKEN_ENCRYPTION_KEY` 保持不变；`pg_restore --no-owner --no-acl` 让角色名不一致也能恢复。
- 发布和备份用同一把 `flock` 互斥，避免 `pg_dump` 持有的锁和 `migrate` 的表结构修改互相卡住；比给迁移设锁超时简单，也不用改 Alembic。
- `backup.sh` 和 `restore.sh` 给 `IMAGE_TAG` 用占位值，而不是绕开 compose 用固定容器名 `docker exec`：容器名依赖 compose 项目名，换目录或改名就失效。
- 手动发布入口复用已发布的镜像，不重新构建，这样回滚不受 CI 是否通过的影响；checkout 被回滚的 sha，保证 compose 文件和镜像是同一版本。

## 交叉检查记录

写完 spec 后由另一个模型做了一次只读交叉检查，找出 18 处没定下来的细节和若干稳健性问题，你选择全部按建议修改，已经合并进 index.md。主要包括：compose 变量插值在全新服务器上会失败、手动发布任务会被跳过且有命令注入风险、备份管道的失败判断、oneshot 服务没有超时、恢复到已迁移的库会冲突、SSH 配置文件名排序导致禁用密码不生效、needrestart 会自动重启 Docker、发布和备份并发。

## 工具搜索结果

决策过程中搜索了和 R2、rclone、age、systemd、Postgres 备份、服务器加固相关的 Agent Skill 和 MCP 服务器（skills.sh 注册表，2026-11-02）。只有 Cloudflare 官方仓库（`cloudflare/skills`）的 skill 有较高的采用量；rclone、age 和 pg_dump 备份都没有可信的专门 skill；R2 也没有确认过的官方 MCP 服务器。是否安装由你决定，结果记在 index.md 的 Follow-up 里。
