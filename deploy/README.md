# 部署手册

Harvester 跑在一台 Lightsail 服务器上（Ubuntu 24.04），所有东西都在部署用户（默认 `ubuntu`）的 `~/harvester/` 里。设计和取舍见 [spec 0009](../docs/specs/0009-deploy-and-keep-running/index.md)。

服务器上的文件：

| 文件 | 从哪来 | 说明 |
|---|---|---|
| `compose.prod.yml`、`deploy.sh`、`backup.sh`、`restore.sh` | 每次发布由 Release 复制 | |
| `bootstrap.sh`、`systemd/` | 第一次用 `scp -r` 复制 | 以后改了要手动再复制并重跑 bootstrap |
| `.env` | 你手动创建，`chmod 600` | 应用配置和密钥 |
| `backup.env` | 复制 `backup.env.example` 后填值，`chmod 600` | R2 密钥和 age 公钥，容器读不到 |
| `.image-tag` | `deploy.sh` 写入 | 当前运行的 sha |
| `.ops.lock` | 自动创建 | 发布、备份、恢复互斥用 |
| `backups/` | bootstrap 创建 | 本机只留最新 2 份加密备份 |

## 一次性准备

这些只做一次，结果存进密码管理器。

1. **Cloudflare R2**：建一个私有桶（比如 `server-backups`）。在桶的设置里加一条生命周期规则：对象上传 14 天后删除。脚本自己从不删 R2 上的文件，过期全靠这条规则。
2. **两个 R2 API token**（都只作用于这一个桶）：
   * 服务器用：权限「对象读写」，填进服务器的 `backup.env`。
   * Mac 用：权限「对象只读」，只用来演练恢复，存在密码管理器，不放到服务器上。
3. **age 密钥**：在 Mac 上 `age-keygen -o harvester-backup.key`。私钥（整个文件内容）存进密码管理器；公钥（`age1...` 那一行）留着填 `backup.env`；然后 `rm harvester-backup.key`。服务器上永远只有公钥。
4. **Lightsail 控制台**：实例防火墙的 IPv4 和 IPv6 两套规则都只开 TCP 22、80、443 和 UDP 443。给实例绑静态 IP，域名的 A（和 AAAA）记录指向它。
5. **GitHub**：仓库 secrets 里有 `DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`。再建一个只有 `read:packages` 权限的 PAT，给服务器 `docker login ghcr.io` 用。

## 第一次上线（含从 Mac 迁移）

1. **确认版本一致**：本机在 main 最新的 sha 上，`cd backend && uv run alembic current` 显示的版本和这个 sha 的最新迁移一致。记下这个 sha。
2. **初始化服务器**：

   ```bash
   ssh ubuntu@<IP> 'mkdir -p ~/harvester'   # 目录不存在时，下面的 scp 会失败
   scp -r deploy/* ubuntu@<IP>:~/harvester/
   ssh ubuntu@<IP>
   sudo ~/harvester/bootstrap.sh ubuntu
   exit   # 重新登录，让 docker 组生效
   ```

   bootstrap 可以放心重跑。它会装 Docker、开 2 GB swap、关掉 SSH 密码和 root 登录（你的 `~/.ssh/authorized_keys` 必须已经有公钥，否则它不改 SSH 并报错退出）、只自动装安全更新、装 age 和 rclone、装好备份定时器。
3. **登录 GHCR**：`docker login ghcr.io -u <GitHub 用户名>`，密码填那个只读 PAT。
4. **写 `~/harvester/.env`**（`chmod 600`）：

   ```bash
   IMAGE_PREFIX=ghcr.io/<owner>/harvester
   SITE_DOMAIN=<你的域名>
   POSTGRES_USER=harvester
   POSTGRES_DB=harvester
   POSTGRES_PASSWORD=<新的随机值，比如 openssl rand -hex 24>
   TOKEN_ENCRYPTION_KEY=<必须和 Mac 本机 .env 里的完全一样>
   ```

   写完把整个 `.env` 存一份到密码管理器（和 age 私钥分开两条）。
5. **写 `~/harvester/backup.env`**：先 `cp ~/harvester/backup.env.example ~/harvester/backup.env && chmod 600 ~/harvester/backup.env`，再把值填进去（模板随上面的 `scp` 一起过去了）。填好后长这样：

   ```bash
   R2_ACCOUNT_ID=<Cloudflare 账号 ID>
   R2_ACCESS_KEY_ID=<服务器用的读写 token>
   R2_SECRET_ACCESS_KEY=<服务器用的读写 token>
   R2_BUCKET=server-backups
   BACKUP_AGE_RECIPIENT=age1...
   ```

6. **Mac 上停掉本机采集并导出**：停掉本机 collector（Ctrl+C），确认日志里有 `collector.stopping`，然后：

   ```bash
   docker compose exec -T postgres pg_dump -Fc -U harvester -d harvester > harvester-migrate.dump
   scp harvester-migrate.dump ubuntu@<IP>:~/
   ```

7. **服务器上恢复**：`~/harvester/restore.sh ~/harvester-migrate.dump`，输入 `RESTORE`。这时还没发布过，脚本会用占位的镜像版本，不影响恢复。
8. **第一次发布**：在 GitHub Actions 页面手动运行 Release，`sha` 填第 1 步那个，`services` 填 `migrate caddy api collector`。
9. **检查**：打开 `https://<域名>`，用原来的密码和 TOTP 登录，消息、频道开关和账号都在，collector 回到「在线」。Discord 第一次看到机房 IP 可能要求验证：账号会变成「Discord 要求验证」，用浏览器登录小号完成验证，再在 dashboard 点「重新连接」。
10. **Mac 收尾**：把本机 `.env` 的 `TOKEN_ENCRYPTION_KEY` 换成新生成的开发密钥（`uv run python -m harvester.crypto genkey`）。这样本机库里的旧 token 解不开，本机 collector 就算被误启动也登录不了小号。本机的数据卷和 `harvester-migrate.dump` 先留着，等服务器第一次定时备份成功、恢复演练也通过之后，再 `docker compose down -v` 并删掉 dump。

## 日常发布

推到 main 就会自动发布：CI 通过后构建镜像，只更新受影响的服务（规则见 spec 0001）。发布等备份结束才开始（最多等 30 分钟），备份也会等发布结束。

**发布 collector 会消耗一次登录额度。** 发布 collector 或重启服务器都会断开 Discord 会话，重新登录算一次（24 小时最多 10 次，spec 0005）。短时间内连续发布 collector 要留意额度。

发布之后 1 到 2 分钟内首页显示「采集进程无响应」是正常的：旧进程退出时不写「已停止」，新进程起来写第一次心跳后提示就消失。

## 回滚和手动重新发布

在 GitHub Actions 页面打开 Release，点「Run workflow」：

* `sha`：一个**已经发布过**的 40 位 sha（镜像必须已经在 GHCR 上）。
* `services`：默认 `caddy api`；回滚一般填 `caddy api collector`。

手动运行不跑检查、不重新构建镜像，复制到服务器的 compose 文件和脚本也来自那个 sha。sha 格式不对、服务不在 `migrate caddy api collector` 里、镜像不存在时，运行直接失败，不会登录服务器。

回滚**默认不带 `migrate`**，而且**不支持回到更早的表结构**（项目没有 downgrade 约定）。只能回滚到和当前表结构兼容的版本。

在服务器上也可以直接：`cd ~/harvester && IMAGE_TAG=<sha> ./deploy.sh caddy api collector`。

## 「已停止」和「无响应」

* **无响应**（首页、设置页账号卡和侧栏都会显示「采集进程无响应」）：collector 进程超过 120 秒没写心跳，或者已经有账号但从来没有过心跳。可能是进程卡住、退出了、正在发布，或者数据库连不上。
* **已停止**：账号被你在设置页停用。这是账号状态，进程还在正常写心跳。
* **未运行**：还没有账号，也从来没有心跳。不标红。

进程退出时 Docker 会自动把它拉起来（`restart: unless-stopped`），不用管。**卡住**（进程还在但不干活）不会被自动重启，要你手动处理。

## 手动重启卡住的 collector

```bash
cd ~/harvester
docker compose -f compose.prod.yml --env-file .env restart collector
docker compose -f compose.prod.yml --env-file .env logs --tail 100 collector
```

`restart` 会断开 Discord 会话，同样消耗一次登录额度。如果 compose 抱怨缺 `IMAGE_TAG`，先 `export $(cat .image-tag)`。

## 重启服务器

服务器只自动安装安全更新，从不自己重启，也不会自动重启 Docker 或其他服务。所以内核或 libc、openssl 这类更新要等你手动重启才完全生效。

查有没有需要重启的：

```bash
cat /var/run/reboot-required 2>/dev/null       # 有这个文件就是内核等更新在等重启
cat /var/run/reboot-required.pkgs 2>/dev/null  # 哪些包要求重启
sudo needrestart -b                            # 哪些服务还在用旧的库
```

重启：`sudo reboot`。所有容器会自动起来，collector 在几分钟内回到「在线」（受登录额度和退避约束）。这同样消耗一次登录额度，挑不急的时候做，大约一个月一次就够。

## 备份

每天 UTC 04:00（北京时间 12:00）之后 5 分钟内自动备份一次；那个时间服务器关着就开机补跑。流程：`pg_dump` 整库，age 公钥加密，rclone 上传到 R2，结果写进 `backup_run` 表，首页「备份」那一行显示最近一次的结果。最近一次成功超过 48 小时，图标栏的首页入口会出现红点。

```bash
sudo systemctl start harvester-backup        # 立刻手动跑一次
journalctl -u harvester-backup -n 50         # 看日志
systemctl list-timers harvester-backup.timer # 下次什么时候跑
ls -l ~/harvester/backups/                   # 本机缓冲（只留最新 2 份）
```

备份里只有数据库，没有 `.env`、`TOKEN_ENCRYPTION_KEY` 和 R2 密钥。拿到备份的人没有 age 私钥就读不了，有了私钥也解不开里面的 Discord token（那要 `TOKEN_ENCRYPTION_KEY`，它在另一条密码管理器记录里）。

## 从备份恢复

1. 在 R2 控制台（或用 Mac 的只读 token 加 rclone）下载要恢复的 `harvester-*.dump.age`，放到服务器上。
2. 从密码管理器里取出 age 私钥，临时存成一个文件（比如 `~/restore.key`，`chmod 600`）。
3. 恢复：

   ```bash
   ~/harvester/restore.sh ~/harvester-20261102T040312Z.dump.age ~/restore.key
   rm ~/restore.key     # 用完立刻删掉
   ```

   脚本会停掉 api 和 collector，要求输入 `RESTORE`，清空 `public` schema 再恢复，所以往已经迁移过的库里恢复旧备份也不会冲突。
4. 恢复完服务不会自动启动。按脚本最后打印的命令发布一次（带 `migrate`，补上备份之后新增的表结构）：

   ```bash
   cd ~/harvester && IMAGE_TAG=<sha> ./deploy.sh migrate caddy api collector
   ```

**服务器整个坏了**：开一台新实例，从「一次性准备」第 4 步开始，跑 bootstrap，从密码管理器放回 `.env` 和 `backup.env`，登录 GHCR，再按上面的步骤恢复，然后手动运行 Release（`migrate caddy api collector`）。大约半小时到一小时，期间不采集。

## 每月演练一次恢复（在 Mac 上）

不演练过的备份不算备份。每月一次，在 Mac 上把最新备份恢复到本地 Postgres，核对行数：

```bash
# 1. 用 Mac 专用的只读 token 下载最新备份（值从密码管理器取，只在这个终端里有效）
export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ENDPOINT=https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com
export RCLONE_CONFIG_R2_ACCESS_KEY_ID=<只读 token> RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=<只读 token>
rclone lsl r2:server-backups | sort -k2,3 | tail -3
rclone copyto r2:server-backups/<文件名> ./latest.dump.age

# 2. 私钥临时存成文件，解密
age -d -i restore.key -o latest.dump latest.dump.age && rm restore.key

# 3. 恢复到本地 Postgres（会清空本地库！先停掉本机 api 和 collector）
docker compose exec -T postgres psql -U harvester -d harvester -v ON_ERROR_STOP=1 \
  -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
docker compose exec -T postgres pg_restore --no-owner --no-acl --role=harvester \
  --single-transaction --exit-on-error -U harvester -d harvester < latest.dump

# 4. 核对行数，和服务器上同样的查询比较（服务器上的数会多出备份之后新采到的）
docker compose exec -T postgres psql -U harvester -d harvester -c \
  "SELECT (SELECT count(*) FROM message) AS message, (SELECT count(*) FROM channel) AS channel, (SELECT count(*) FROM discord_account) AS account;"

# 5. 收尾
rm latest.dump latest.dump.age
```

服务器上的对照查询：

```bash
cd ~/harvester && docker compose -f compose.prod.yml --env-file .env exec -T postgres \
  psql -U harvester -d harvester -c "SELECT (SELECT count(*) FROM message), (SELECT count(*) FROM channel), (SELECT count(*) FROM discord_account);"
```

本地库里的 Discord token 用的是服务器的密钥加密的，本机的开发密钥解不开，所以本机 collector 不会用它登录。

## R2 和两个 token

* 桶是私有的，生命周期规则「上传 14 天后删除」，所以 R2 上最多约 14 份。
* 服务器 token：只限这个桶的对象读写，在 `backup.env` 里。服务器被攻破时，攻击者可以用它删除备份（没开桶锁，这是已知的取舍）。
* Mac token：只限这个桶的只读，只用来演练和灾后下载，在密码管理器里。
* 任何一个泄露，在 Cloudflare 控制台撤销后重建，更新对应的地方即可。

## 附件桶和两个 token

附件（消息里的图片和文件）存在另一个私有 R2 桶里，和备份桶分开。文件按 `<频道 ID>/<年月>/<SHA-256>.<扩展名>` 存放，缩略图在同一个目录的 `thumbs/` 下。

第一次上线前，在 Cloudflare 里做一次（你来做，我不会替你点）：

1. 建一个桶 `harvester-media`。桶本身不开 `r2.dev`，只通过自定义域名 `media.norafu.com` 公开读（见下面「附件的公开域名」）；**不加生命周期规则**（程序任何时候都不会删文件，桶里的文件就是附件的唯一副本）。
2. 建两个只作用于这个桶的 API token：
   * 对象读写：给 collector，写进 `.env` 的 `MEDIA_R2_WRITE_ACCESS_KEY_ID` 和 `MEDIA_R2_WRITE_SECRET_ACCESS_KEY`。
   * 对象只读：给 api，写进 `MEDIA_R2_READ_ACCESS_KEY_ID` 和 `MEDIA_R2_READ_SECRET_ACCESS_KEY`。
3. 服务器 `~/harvester/.env` 里再加这两项，并把四个密钥的副本存进密码管理器：

```bash
MEDIA_R2_ACCOUNT_ID=<Cloudflare 账号 ID，和备份用的是同一个>
MEDIA_R2_BUCKET=harvester-media
```

`compose.prod.yml` 只把读写 token 给 collector，只把只读 token 给 api，两个进程拿不到对方的密钥。线上的 `MEDIA_STORAGE` 固定是 `r2`，缺任何一项，对应的进程启动时就报错退出。

发布这个功能时，三个服务都要更新（迁移会给已有的附件和 embed 图补建下载任务）：

```bash
cd ~/harvester && IMAGE_TAG=<sha> ./deploy.sh migrate api collector
```

发布后 collector 会自己开始补下载，从最近的消息往回补。旧附件的 Discord 链接大多已过期，要先向 Discord 换新链接，节奏是每小时最多 600 个，所以库存多的话要补很久。进度看 dashboard 的「附件存储」设置页，首页「附件下载」一行能看到下载任务是否还在跑。

* 附件**不进** `pg_dump`，也不另做备份：R2 自己有冗余。恢复旧的数据库备份后，桶里会多出数据库不认识的文件，无害。
* 空间满了会停止下载并在图标栏显示红点，在「附件存储」页调高总容量即可继续。
* 只读 token 或读写 token 泄露：在 Cloudflare 控制台撤销、重建，更新 `.env` 后重启对应的服务。

## 附件的公开域名（导出用）

频道导出（spec 0014）里的附件和 embed 图片链接写成 `https://media.norafu.com/<频道 ID>/<年月>/<SHA-256>.<扩展名>`，不用登录就能打开，方便交给外部 AI 读取。dashboard 自己仍然走 1 小时有效的签名链接，不受影响。

这些链接**永久有效，不能单独收回**：谁拿到导出文件，谁就能一直打开里面的文件。文件名里的 SHA-256 猜不到，域名也不列目录，所以没拿到导出文件的人找不到它们。

### Cloudflare 控制台步骤（你来做，我不会替你点）

建议在发布带这个功能的 api 之前做完：

1. 核对 DNS（`norafu.com` 已在 2026-11-06 迁到 Cloudflare）：原来的记录都在（`harvester.norafu.com` 的 A 记录，邮件用的 MX、TXT 记录，如果有）。`harvester.norafu.com` 设成 **DNS only（灰色云）**，因为 Caddy 自己申请 HTTPS 证书，走 Cloudflare 代理（橙色云）可能让证书续期和 SSE 长连接出问题。`dig +short harvester.norafu.com` 应该返回服务器的静态 IP，dashboard 能正常打开。
2. R2 → `harvester-media` → Settings → Custom Domains，绑定 `media.norafu.com`（Cloudflare 会自动建这条记录，走代理）。
3. 同一页确认 `r2.dev` 公开地址是关着的。
4. Rules → Transform Rules → Modify Response Header，条件 `Hostname equals media.norafu.com`，设置两个响应头：`Content-Security-Policy: sandbox` 和 `X-Content-Type-Options: nosniff`。这个域名和 dashboard 同站（都在 `norafu.com` 下），这条规则防止恶意的 HTML 或 SVG 附件在上面运行脚本。代码里没有第二道兜底，所以别删它。
5. 让外部 AI 取得到文件：在 Security 里确认这个 zone 的 AI 爬虫屏蔽（AI bot blocking）和 Bot Fight Mode 是关的；要开着的话，就给 `media.norafu.com` 加一条跳过规则，不然外部 AI 会拿到 403 或质询页。
6. 检查：

```bash
curl -I https://media.norafu.com/<一个已存的 object_key>   # 200，带上面两个头
curl -I https://media.norafu.com/                          # 不列出文件
```

`object_key` 可以在数据库的 `stored_file.object_key` 里找一个。

### 上线顺序

1. 做完上面的 Cloudflare 步骤，`curl -I` 确认通过。
2. **合并之前**，给服务器 `~/harvester/.env` 加一行：

```bash
MEDIA_PUBLIC_BASE_URL=https://media.norafu.com
```

   `compose.prod.yml` 对它用的是 `:?` 写法：`.env` 里少了它，整个 compose 文件都读不了，collector 的发布和其他用 compose 的运维脚本也会一起失败，而合并后 CI 会自动发布。值只能是 `https://<主机名>`，不能带路径，带了 api 启动就报错退出。

3. 合并，发布 api：`cd ~/harvester && IMAGE_TAG=<sha> ./deploy.sh api`。
4. 回滚：发回上一个镜像就行，`.env` 里留着这个变量不影响旧版本。

### 以后要删文件

程序从不删 R2 里的文件。你手动删了某个文件后，Cloudflare 的缓存还会让它的链接能打开一阵子，需要时在 Cloudflare 控制台对那个网址做 Purge。

## 密钥副本在哪里

| 东西 | 在哪里 | 不在哪里 |
|---|---|---|
| `.env` 全文（含 `TOKEN_ENCRYPTION_KEY`、数据库密码） | 服务器 `~/harvester/.env`；密码管理器一条 | git、备份文件 |
| age 私钥 | 只在密码管理器（恢复时临时放一下，用完删除） | 服务器 |
| age 公钥 | 服务器 `backup.env` | |
| 服务器 R2 token（读写） | 服务器 `backup.env` | 密码管理器以外的任何地方 |
| Mac R2 token（只读） | 密码管理器 | 服务器 |
| 附件桶 R2 token（读写 / 只读） | 服务器 `~/harvester/.env`（读写只给 collector，只读只给 api）；密码管理器各一条 | git、日志 |
| GHCR 只读 PAT | 服务器 `~/.docker/config.json`（`docker login` 写入） | |
