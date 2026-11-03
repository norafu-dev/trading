# Verify: 睡眠登录保护和日志文件 · spec 0007 · updated 2026-11-02
_Steps derived from spec 0007 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [ ] 配 `LOG_DIR=logs`，启动 api 和 collector → `backend/logs/` 下有 `api.log` 和 `collector.log`，内容和 stdout 一样 → AC-7
- [ ] 不配 `LOG_DIR` 启动 → 不建目录，只写 stdout → AC-7
- [ ] 断开网络再恢复 → `collector.connection_failed` 带 `kind`、`close_code`、`http_status`，没有 token 和响应内容 → AC-11
- [ ] 分别触发启动、断线后重新登录、RESUME 被拒 → `collector.identify_reserved.reason` 依次是 `initial`、`after_disconnect`、`resume_rejected` → AC-11
- [ ] 合盖过夜 → `collector.sleep_detected` 出现，`gap_seconds` 和合盖时长相符 → AC-1
- [ ] 合盖过夜 → 后台唤醒期间没有 `identify_reserved`，`discord_login_attempt` 没有新行 → AC-2
- [ ] 开盖后会话已过期 → 连续醒满 90 秒才出现 `identify_reserved`；期间再合盖会重新计时 → AC-2
- [ ] 短暂合盖、会话仍有效 → 直接 RESUME，额度行数不变 → AC-3
- [ ] 没有睡眠时重启 collector → 没有额外等待 → AC-4
- [ ] 等待期间看 dashboard → 显示“连接中断，正在重试”；这时换 token 或停用，等待立刻结束 → AC-5
- [ ] 额度用完 → 状态是 `login_paused`，`collector.login_budget_exhausted.retry_at` 和界面的恢复时间一致，覆盖段的关闭时间是断线时间 → AC-6、AC-11
- [ ] 线上环境 → 没有配置 `LOG_DIR`，容器里没有日志文件，只有 Docker 的 stdout 日志 → AC-7

## Commands
- [ ] `cd backend && uv run pytest tests/test_logging.py tests/collector/test_wake_guard.py` → 全部通过（轮转、脱敏、权限、时钟判断）→ AC-1、AC-2、AC-7、AC-8、AC-9、AC-10
- [ ] `cd backend && LOG_DIR=/dev/null/x uv run python -m harvester.api` → 非零退出 → AC-10
- [ ] `cd backend && LOG_DIR=/dev/null/x uv run python -m harvester.collector` → 非零退出，不会连接 Discord → AC-10
- [ ] `git check-ignore backend/logs/api.log` → 命中 `logs/` → AC-9

## Acceptance-criteria coverage
- AC-1 合盖过夜 · AC-2 后台唤醒和开盖等待 · AC-3 短暂合盖 · AC-4 无睡眠重启 · AC-5 等待期间的界面和打断 · AC-6 额度用完 · AC-7 配置和未配置 `LOG_DIR` · AC-8 pytest 轮转 · AC-9 pytest 脱敏和权限、`git check-ignore` · AC-10 不可写目录 · AC-11 三种原因、失败字段和 `retry_at`
