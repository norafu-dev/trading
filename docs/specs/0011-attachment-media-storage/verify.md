# Verify: 附件与图片保存 · spec 0011 · updated 2026-11-04
_Steps derived from spec 0011 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [x] 本地 `MEDIA_STORAGE=local`，在开着采集的频道发一条带 PNG 的消息 → 几秒内消息里出现图片，`backend/media-dev/<频道ID>/<年月>/` 下有原图 → AC-1, AC-4, AC-16, AC-20
- [x] 发一张长边超过 800 像素的图 → 消息里是缩略图，点开是原图大图，大图里有「下载原文件」；发一张小图 → 没有 `thumbs/` 文件 → AC-5, AC-15
- [x] 发一个视频、一个音频、一个 txt、一个 SVG → 视频音频直接是播放器；txt 和 SVG 是文件条，点击下载；SVG 不在页面里内联显示 → AC-14, AC-15
- [x] 同一个频道同一个月发两次内容相同的图 → 目录下只有一份文件，设置页已用容量只多一份 → AC-4
- [x] 转发一条带图片的消息 → 转发块里的图片同样显示 → AC-2, AC-15
- [ ] 发一条带 Discord 托管大图的链接 embed → embed 大图走自己的存储；发一条第三方网站图片的 embed → 仍然直接加载，没有 media 行 → AC-2, AC-15
- [ ] 编辑那条 embed 消息让大图换成另一张 → 那一行回到「保存中」再变成新图 → AC-13
- [x] 删除一条带附件的消息 → 消息标记已删除,图片仍能看到 → AC-13
- [x] 登出后直接打开 `/api/media/<id>` → 401 → AC-14
- [ ] 设置页 `/settings/media`:把单个文件上限设成 0、1025,总容量设成 0、10001 → 都不保存并在输入框旁说明;设成合法整数 → 保存成功,几秒内生效,不用重启 collector → AC-8
- [x] 把单个文件上限调到 1 MB 后发一个 2 MB 附件 → 显示「超过上限，未保存」;再调高到 10 MB → 几秒内自动变成已保存 → AC-6, AC-9
- [ ] 把总容量调到 1 GB 并让已用容量达到它(或临时改库里的数)→ 新附件显示「空间不足，未保存」,图标栏「附件存储」入口出现红点,设置页区块标红;调高总容量后红点消失 → AC-7, AC-9
- [ ] 让一个附件失败(比如断网下载)→ 消息里显示「保存失败」和「重试」;点重试 → 变「保存中」;设置页「重试全部失败」在失败数为 0 时禁用 → AC-10, AC-15, AC-17
- [ ] 对「超过上限」或「空间不足」的行没有重试按钮;直接调接口 → 409 → AC-17
- [ ] 关掉一个频道的采集,再在里面发附件 → 附件保持「保存中」,设置页「待保存」旁说明有几个所在频道已关闭;重新打开 → 自动继续 → AC-23
- [ ] 首页「附件下载」一行显示最近心跳的相对时间;停掉 collector 进程以外的下载任务不好模拟,改为暂停 30 分钟时显示「已暂停到 …」 → AC-22
- [x] 窄屏(手机宽度)下查看大图、视频、音频 → 不横向溢出 → AC-15

## Commands
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright` → 全部通过 → AC-21
- [x] `cd backend && uv run pytest` → 全部通过(连真实 PostgreSQL) → AC-1 到 AC-23
- [x] `cd backend && uv run python -m harvester.api.export_openapi && cd ../web && pnpm gen:api && git diff --exit-code -- ../backend/openapi.json src/lib/api/schema.d.ts` → 没有差异 → AC-21
- [x] `cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm check:contrast && pnpm build` → 全部通过 → AC-21
- [ ] 在有旧附件和旧 embed 的库上 `uv run alembic upgrade head`,再 `downgrade -1` 后 `upgrade head` → `media` 行数不变且不重复;`SELECT count(*) FROM media` 等于附件数加 Discord 托管的 embed 图数 → AC-12
- [ ] `unset MEDIA_STORAGE` 后启动 api 和 collector → 都报错退出;`MEDIA_STORAGE=r2` 缺任意一项 R2 配置 → 对应进程报错退出 → AC-20
- [ ] 启动 collector 前往 `MEDIA_TMP_DIR` 放一个文件,并把某行 `claimed_at` 设成非空 → 启动后文件消失、`claimed_at` 清空、`attempts` 不变 → AC-18
- [ ] `grep -rE 'ex=|hm=|is=' backend/logs/` 和 `docker compose logs collector api` → 搜不到签名链接和 R2 密钥 → AC-19
- [ ] 在 Cloudflare 控制台确认两个 token 只作用于 `harvester-media`,只读 token 没有写权限;`docker compose -f compose.prod.yml config` 里 api 只有 `MEDIA_R2_READ_*`,collector 只有 `MEDIA_R2_WRITE_*` → AC-19

## 真实 Discord 和真实 R2(上线后手动)
- [ ] 用真实小号收一条带图片的新消息 → 15 秒内变成已保存,R2 桶里有 `<频道ID>/<年月>/<哈希>.<扩展名>` → AC-1, AC-4
- [ ] 一条几天前的旧附件 → 先换新链接再下载成功,`attempts` 没增加 → AC-11, AC-12
- [ ] Discord refresh-urls 的请求和响应格式、每批上限、已删除消息的附件是否还能换到新链接,和 spec 写的一致;不一致就回 `/architect` 调整 AC-11 → AC-11(spec Follow-up)
- [ ] R2 签名链接上的 `response-content-disposition` 和 `response-content-type` 生效(SVG 以下载方式打开,文件名正确);不生效就改为上传时写元数据 → AC-14(spec Follow-up)
- [ ] 当前 boto3 版本在 R2 上用 `request_checksum_calculation="when_required"` 上传不报错;不需要这两个设置就去掉 → AC-14(spec Follow-up)
- [ ] 补下载跑起来后,观察每小时换链接不超过 600 个,日志里没有 429 风暴;首页「附件下载」心跳正常 → AC-11, AC-22

## Acceptance-criteria coverage
- AC-1 · 自动化 `test_media_worker.py`、`test_media.py`;手动第 1 条 · AC-2 · `test_pure.py`、`test_media.py` · AC-3 · `test_redirect_is_gone_and_never_followed`
- AC-4 · `test_same_content_same_month_is_stored_once`、`test_storage_keys` · AC-5 · `test_thumbnail_rules`、`test_big_image_gets_a_thumbnail…`
- AC-6 · `test_declared_size_over_limit…`、`test_unknown_size_is_aborted…` · AC-7 · `test_quota_counts_the_thumbnail`、`test_system_status_media_block`
- AC-8, AC-9 · `test_media_settings.py`、`media-settings-panel.test.tsx` · AC-10 · `test_failure_classification`、`test_fifth_retryable_failure_becomes_failed`、`test_403_burst_pauses_the_worker`
- AC-11 · `test_refresh_*`、`test_unavailable_*`、`test_sixty_rows…`、`test_gateway_handle_follows_the_session` · AC-12 · `test_newest_messages_are_claimed_first` + 迁移补建(命令里那一条)
- AC-13 · `test_changed_embed_image_resets_the_row` 等 · AC-14 · `test_media_routes.py`、`test_presigned_url_overrides_response_headers`
- AC-15 · `media-attachment.test.tsx`、`to-message-view.test.ts` · AC-16 · `test_download_stores_file_and_notifies`(通知在同一个事务里)
- AC-17 · `test_retry_rules`、`test_retry_all_failed_leaves_gone_alone` · AC-18 · `test_stale_claim_result_is_discarded`、`test_startup_releases_claims…`、`test_put_file_uses_multipart_capable_upload`
- AC-19 · `test_each_process_only_sees_its_own_token`、`test_logs_never_contain_link_signatures` · AC-20 · `test_storage_config.py`
- AC-21 · 命令那几条 · AC-22 · `describe-system.test.ts`、`test_system_status_media_block` · AC-23 · `test_closed_channel_rows_are_not_claimed`、`test_pending_count_splits_out_closed_channels`
