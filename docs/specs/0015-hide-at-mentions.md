# 0015 · 去掉 dashboard 里的 @ 提及

**Status**: Assumed
**Date**: 2026-11-06
**Authorized by**: Nora Fu, during /develop

## Owed decision
spec 0003 AC-9 规定 @用户 显示查到的名字、@身份组 显示「@身份组」、所有提及都用高亮底色。你觉得 @ 的部分对你完全没用，想在 dashboard 里直接去掉。这推翻了一条已经 Accepted 的验收标准，还牵涉接口（为提及批量加载的 `users`）和 collector（为被提及的人写 `discord_user` 表），所以要记一笔。

## Assumption built on
- 数据库里的原始正文不动，只在显示时去掉。搜索和导出本来就按 spec 0013 AC-6 的规则去掉了提及，这里不改。
- 频道页、搜索页显示的正文、转发正文、embed 描述和字段值、回复引用的摘要里，`<@123>`、`<@!123>`、`<@&123>`、`@everyone`、`@here` 整个去掉。去掉后同一行里多出来的空格合成一个，行首行尾的空格去掉；一行只剩提及的，这一行整个去掉。整条消息只剩提及的，显示成空正文。
- `#频道`（`<#123>`）保留，照旧显示频道名和高亮底色，所以 `--mention` token 留着。
- 接口 `GET /api/channels/{id}/messages` 和搜索接口不再返回 `users`，只返回 `channels`。
- collector 不再从 `message.mentions` 取被提及的用户，也不再为他们写 `discord_user`。已经写进去的行留着不删。
- 万一有漏网的用户、身份组、everyone、here 提及节点到了渲染这一步，渲染成空，不显示任何文字。

## Code area
- `web/src/lib/discord-markdown/`（去掉提及的纯函数、`MessageContent`、`renderers.tsx`）
- `web/src/components/messages/`、`web/src/components/channels/use-channel-messages.ts`、`web/src/components/search/`、`web/src/app/dev/preview/`、`web/src/dev/fake-messages.ts`
- `backend/src/harvester/db/messages.py`、`db/search.py`、`db/ingest.py`、`db/snapshots.py`、`collector/discord_client.py`、`api/message_routes.py`、`api/search_routes.py`
- `backend/openapi.json`、`web/src/lib/api/` 生成的类型

## Requirements
- 频道页和搜索页看不到任何 @用户、@身份组、@everyone、@here，也不留多余空格或空行。
- `#频道` 照旧显示名字。
- 接口响应里没有 `users`。
- 新采集的消息不会因为提到某人而往 `discord_user` 里多写一行。

## Ratify
This decision was recorded by /develop, not deliberated. Run `/architect hide at mentions`
to deliberate and ratify it. Until then it stays flagged as an owed decision; it does not block marking the feature `done`.
