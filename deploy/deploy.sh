#!/usr/bin/env bash
# Runs on the server, from the directory holding compose.prod.yml and .env.
#
#   IMAGE_TAG=<git sha> ./deploy.sh [service ...]
#
# Services default to "caddy api". Add "collector" only when collector code, db models,
# shared config, crypto or logging, migrations or backend dependencies changed (CI works this out), so the Discord
# session is not restarted for dashboard only releases. "migrate" runs migrations first.
# Rollback: IMAGE_TAG=<old sha> ./deploy.sh caddy api collector
set -euo pipefail

cd "$(dirname "$0")"
: "${IMAGE_TAG:?IMAGE_TAG is required (git sha)}"
export IMAGE_TAG

# 发布和备份互斥（spec 0009）：备份进行中就等它结束，最多等 30 分钟。
exec 9>.ops.lock
if ! flock -w 1800 9; then
  echo "deploy: timed out waiting for .ops.lock (a backup is still running)" >&2
  exit 1
fi

services=("$@")
if [ ${#services[@]} -eq 0 ]; then
  services=(caddy api)
fi

compose=(docker compose -f compose.prod.yml --env-file .env)

run_migrate=false
targets=()
for s in "${services[@]}"; do
  if [ "$s" = migrate ]; then run_migrate=true; else targets+=("$s"); fi
done

pull=("${targets[@]}")
$run_migrate && pull+=(migrate)
"${compose[@]}" pull "${pull[@]}"

"${compose[@]}" up -d --wait postgres

if $run_migrate; then
  "${compose[@]}" run --rm migrate
fi

if [ ${#targets[@]} -gt 0 ]; then
  "${compose[@]}" up -d --no-deps "${targets[@]}"
fi

# Remember what is running, for manual compose commands later.
echo "IMAGE_TAG=${IMAGE_TAG}" > .image-tag
"${compose[@]}" ps
