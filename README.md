# Harvester

只给自己用的 Discord 频道采集器。架构见 [spec 0001](docs/specs/0001-stack-and-architecture/index.md)，路线图见 [scope](docs/scope/scope.md)。

```
backend/   Python 包 harvester：collector 和 api 两个进程（uv）
web/       Next.js 静态导出的 dashboard（pnpm）
deploy/    Caddy、生产 compose、deploy.sh
```

## 本地运行

需要 Docker、[uv](https://docs.astral.sh/uv/)、Node（版本见 `web/.nvmrc`）和 pnpm。

```sh
cp .env.example .env && chmod 600 .env    # 改一个随机密码：openssl rand -hex 24
docker compose up -d                      # postgres（127.0.0.1:5432）和 caddy（127.0.0.1:8080）

cd backend
uv sync
uv run alembic upgrade head
uv run python -m harvester.api            # 终端 1，:8000
uv run python -m harvester.collector      # 终端 2

cd web
pnpm install
pnpm dev                                  # 终端 3，:3000
```

浏览器打开 <http://localhost:8080>（经 Caddy 同源访问，和线上一致）。

提交前检查（只需装一次）：`uv tool install pre-commit && pre-commit install`。之后每次 commit 会对改动的文件跑 ruff、Biome，再跑 pyright 和 tsc。

## 常用命令

| 做什么 | 命令 |
|---|---|
| 后端 lint / 格式 / 类型 | `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright` |
| 新建迁移 | `cd backend && uv run alembic revision --autogenerate -m "..."` |
| 改了接口后同步前端类型 | `cd backend && uv run python -m harvester.api.export_openapi && cd ../web && pnpm gen:api` |
| 前端 lint 和格式（Biome） | `cd web && pnpm lint`（检查）· `pnpm format`（自动修正） |
| 前端类型检查 / 构建 | `cd web && pnpm typecheck && pnpm build` |
| 手动跑全部提交前检查 | `pre-commit run --all-files` |
| 构建镜像 | `docker build -t harvester-backend backend` · `docker build -f deploy/caddy.Dockerfile -t harvester-caddy .` |

## 发布

推到 `main` 后 GitHub Actions 构建镜像推到 GHCR，再 SSH 到服务器执行 `deploy/deploy.sh`。只改 dashboard 或 api 时不会重启 collector（规则见 spec 0001「发布规则」）。回滚：`IMAGE_TAG=<旧 sha> ./deploy.sh caddy api collector`。
