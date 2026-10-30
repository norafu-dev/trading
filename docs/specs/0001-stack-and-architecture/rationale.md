# 0001. 技术栈与整体架构：决策记录

构建时不需要读这个文件。它记录为什么这样选，给以后回头看的人用。

## Context

> ⚠️ Premise note: 你后面计划加 AI 分析和交易所下单。下单涉及真钱，和一个违反 Discord 服务条款的 selfbot 跑在同一台机器上，服务器一旦被攻破，交易所密钥也会一起泄露。这个 spec 只定基础并为它们预留位置，不设计它们；下单值得单独跑一次 `/architect`，把密钥存放、权限最小化和审计放在第一位。(basis: 涉及资金的操作要最小权限和审计)

这是一个只给你自己用的 Discord 频道采集工具（见 `docs/scope/scope.md`）。一个 selfbot 用你的小号 24 小时挂在 Discord 上，把勾选频道的新消息存下来，按时间段补抓历史，保留编辑和删除的原文，并在一个私人 dashboard 上实时显示。数据量不大：十几个频道，每天几千条，一年大约百万条。(basis: 你在本次 `/architect` 中的回答)

起作用的几股力量：

- **采集不能断。** 每次重新登录都是一次可疑行为，掉线期间的消息还要事后补抓。凡是会让 collector 频繁重启的设计，都在直接增加封号风险。(basis: Discord 服务条款禁止自动化用户账号)
- **后面会长出新的写入方。** 实时采集、历史采集、以后的 AI 分析和下单，都会同时写库；下单还需要可靠的事务。
- **一个人运维。** 你是唯一的开发者和运维，组件越少越好，出问题时要能一眼看出是哪一块。
- **你的偏好。** 后端用 Python（你最熟悉），前端用 Next.js（你指定的），服务器要大厂、便宜、离大阪近。
- **库的生态。** selfbot 库在被 Discord 不断修补的灰色地带里，维护情况直接决定项目能活多久。

不现在定下来的话，第 3 项数据模型、第 5 项访问保护、第 6 项账号连接都没法开始，它们全都依赖存储和进程怎么划分。

## Options considered

### Option 1: Python 多进程 + Next.js 静态前端 + PostgreSQL，单台 Lightsail（选中）

`collector` 和 `api` 两个 Python 进程共用一个包和一套模型，只通过 PostgreSQL 协作（表、`LISTEN/NOTIFY`、任务表）。Next.js 静态导出，由 Caddy 提供。全部服务用 Docker Compose 跑在一台 Lightsail 上。

**Pros**:
- 改 dashboard 不影响采集；以后加 AI 和下单就是加进程。
- PostgreSQL 一个组件同时解决并发写、事务、通知、队列和向量检索。

**Cons**:
- Python 加 TypeScript 两套工具链。
- 要自己运维数据库和备份。

### Option 2: Python 单进程 + SQLite + 服务端模板（HTMX）

一个 asyncio 进程同时跑 selfbot 和 FastAPI，SQLite 存数据，Jinja2 加 HTMX 写界面，全部 Python。

**Pros**:
- 组件最少，一个语言，备份就是复制一个文件，内存占用最小。
- 对当前的数据量完全够用。

**Cons**:
- 改一行界面也要重启 collector，小号反复重登。
- SQLite 同一时间只有一个写入者，加 AI 和下单后会互相等待，大概率要迁移一次数据库。
- 不是你想用的前端框架。

### Option 3: 全栈 TypeScript（Node selfbot + Next.js）

用 `discord.js-selfbot-v13` 连 Discord，后端和前端都用 TypeScript，可以共用类型。

**Pros**:
- 一种语言，前后端类型天然共享。

**Cons**:
- `discord.js-selfbot-v13` 的 GitHub 仓库已在 2025-10-11 归档，不再维护；selfbot 库一停更，Discord 一改协议就会失效。(basis: 本次联网查证)
- 你更熟悉 Python。

### Option 4: 托管平台（Railway、Fly.io 一类）+ 托管 PostgreSQL

同样的进程划分，但跑在托管平台上，数据库用平台的托管服务。

**Pros**:
- 不用管服务器、系统更新和数据库备份。

**Cons**:
- collector 这类常驻长连接进程加托管数据库，月费通常比一台 $24 的机器高。
- 你明确想要大厂；而且平台的出口 IP 可能变化，对用户账号登录不友好，以后给交易所做 IP 白名单也难。

## Rationale

选 Option 1，因为它是唯一同时满足「采集不能断」和「后面会长出新的写入方」的方案。Option 2 在今天最简单，但它把 collector 和界面绑在一个进程里，正好违背采集不能断这一条；SQLite 的单写入者限制又会在加下单时逼出一次迁移，这是最贵的返工（数据所在的地方）。(basis: 数据模型和存储是最贵的返工)

进程拆分只拆到「同一份代码、多个入口」为止，不是微服务：没有服务间 HTTP，没有消息中间件，所有协作都走一个 PostgreSQL。这样既有隔离的好处，又只是一个人能运维的规模。(basis: monolith first，组件少才运维得动)

你选了 Next.js 而不是推荐的 HTMX。Next.js 完全可行，代价是多一套 TypeScript 工具链。我们用静态导出把代价压到最低：线上不跑 Node 进程，认证只在 FastAPI 一处做，接口类型从 OpenAPI 自动生成。

服务器选 Lightsail 而不是 EC2 或 Google Cloud，是因为对一台常驻的小机器来说，固定月价全包（IPv4 和流量都含）比按量计费便宜，账单也简单；这个项目用不到 EC2 的扩容能力。日本本土厂商（Sakura 大阪 ¥3,740/月）更便宜、更近，但你更想要大厂。

## References

**Project sources**:
- `docs/scope/scope.md`：第 1 项「技术栈与架构」的完成标准，项目的 Tracer Bullet 构建方式，以及封号风险提示。
- 你在本次 `/architect` 中的回答：Python、两个进程、Docker Compose、自有域名加 Caddy、每天几千条消息、以后加 AI 分析和下单、Next.js 静态导出、Lightsail 东京、`.env` 密钥、SSH 发布、structlog、先不接外部告警。

**Practices & standards**:
- Monolith first：先用一份代码，只按运行特性拆进程，不拆服务。
- 数据模型和存储是最贵的返工：一开始就选能撑住以后写入方的数据库。
- 数据库队列优先：吞吐量真的不够时才引入专门的消息中间件。
- Boring technology：PostgreSQL、SQLAlchemy、Docker Compose、Caddy 都是故障模式众所周知的老工具。
- 涉及资金的操作要最小权限、幂等和审计。
- Discord 服务条款禁止自动化用户账号，所以减少重登、限速和用小号是正式需求。

**Links** (web verified，2026-10-24 和 2026-10-28 查证):
- discord.py-self 发布记录（v2.1.0，2026-01-18）: https://github.com/dolfies/discord.py-self/releases
- discord.py-self on PyPI: https://pypi.org/project/discord.py-self/
- discord.js-selfbot-v13（已归档）: https://github.com/aiko-chan-ai/discord.js-selfbot-v13
- FastAPI SSE 参考文档: https://fastapi.tiangolo.com/reference/sse/
- FastAPI on PyPI（0.162.0）: https://pypi.org/project/fastapi/
- SQLAlchemy on PyPI（2.1.6，要求 Python 3.11 以上）: https://pypi.org/project/SQLAlchemy/
- Alembic on PyPI（1.20.1）: https://pypi.org/project/alembic/
- uv on PyPI（0.13.5）: https://pypi.org/project/uv/
- AWS Lightsail 价格: https://aws.amazon.com/lightsail/pricing/
- Sakura VPS 规格与价格: https://vps.sakura.ad.jp/specification/
- DigitalOcean Droplet 价格: https://www.digitalocean.com/pricing/droplets
- Hetzner Cloud: https://www.hetzner.com/cloud/
- Oracle Cloud 免费资源说明: https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm

## 附：服务器价格调研（2026-10-28）

目标规格约 2 vCPU / 4 GB。只有标「官方」的价格是在厂商官网看到的，其余来自第三方，可能已变化。

| 厂商 | 最近机房 | 月费 | 备注 |
|---|---|---|---|
| AWS Lightsail（选中） | 东京（大阪未确认） | $24（官方） | 含 IPv4 和 4 TB 流量，80 GB SSD |
| Sakura VPS 4GB | 大阪 | ¥3,740（官方，含税） | 4 核，200 GB，流量不限 |
| Xserver VPS 4GB | 日本 | 约 ¥1,700 到 2,600（未确认） | |
| KAGOYA VPS 4GB | 日本 | 约 ¥1,760（未确认） | |
| ConoHa VPS 4GB | 日本 | 约 ¥3,420 加 10%（未确认） | |
| Vultr | 大阪 / 东京 | 约 $24 到 36（未确认） | |
| AWS EC2 t3.medium | 大阪 | 约 $40 加硬盘和 IPv4（未确认） | |
| Google Cloud e2-medium | 大阪 | 约 $31 加硬盘（未确认） | |
| DigitalOcean | 新加坡 | $24（官方） | 日本无机房 |
| Hetzner | 新加坡 | 约 $8（未确认） | 日本无机房 |
| Oracle 永久免费 A1 | 大阪（未确认） | $0 | ARM，常开不出机器，闲置会被回收 |
