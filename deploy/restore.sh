#!/usr/bin/env bash
# 把一个备份恢复到服务器的 Postgres（spec 0009 AC-9）：
#
#   ~/harvester/restore.sh <file.dump>
#   ~/harvester/restore.sh <file.dump.age> <age 私钥文件>
#
# 会先停 api 和 collector，清空 public schema 再恢复，所以往已经迁移过的库里恢复旧备份也不冲突。
# 恢复后不自动启动服务，按最后打印的命令发布一次（带 migrate）。私钥文件用完立刻删掉。
set -euo pipefail

usage() {
  echo "usage: $0 <file.dump | file.dump.age> [age identity file]" >&2
  exit 2
}

[ $# -ge 1 ] || usage
dump_file=$(realpath "$1")
identity=${2:-}
[ -r "$dump_file" ] || { echo "restore: cannot read ${1}" >&2; exit 1; }
if [[ "$dump_file" == *.age ]]; then
  [ -n "$identity" ] || { echo "restore: an .age file needs the age identity (private key) file" >&2; exit 2; }
  identity=$(realpath "$identity")
  [ -r "$identity" ] || { echo "restore: cannot read the identity file" >&2; exit 1; }
fi

cd "$(dirname "$0")"

env_value() {
  local file=$1 key=$2 line
  line=$(grep -E "^${key}=" "$file" | tail -n 1 || true)
  line=${line#*=}
  line=${line%\"}; line=${line#\"}
  line=${line%\'}; line=${line#\'}
  printf '%s' "$line"
}

POSTGRES_USER=$(env_value .env POSTGRES_USER)
POSTGRES_DB=$(env_value .env POSTGRES_DB)
if [ -z "$POSTGRES_USER" ] || [ -z "$POSTGRES_DB" ]; then
  echo "restore: POSTGRES_USER and POSTGRES_DB must be set in .env" >&2
  exit 1
fi

# compose.prod.yml 要求 IMAGE_TAG；这里只停服务和 exec 进 postgres，不拉镜像。
IMAGE_TAG=none
if [ -r .image-tag ]; then
  IMAGE_TAG=$(env_value .image-tag IMAGE_TAG)
fi
export IMAGE_TAG

compose=(docker compose -f compose.prod.yml --env-file .env)

echo "This replaces EVERYTHING in database '${POSTGRES_DB}' with:"
echo "  ${dump_file}"
read -r -p "Type RESTORE to continue: " answer
if [ "$answer" != RESTORE ]; then
  echo "restore: cancelled"
  exit 1
fi

# 和发布、备份互斥。
exec 9>.ops.lock
if ! flock -w 1800 9; then
  echo "restore: timed out waiting for .ops.lock" >&2
  exit 1
fi

"${compose[@]}" stop api collector
"${compose[@]}" up -d --wait postgres

# vector 扩展随 public 里的对象一起删除，由 dump 里的 CREATE EXTENSION 重建。
"${compose[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'

restore_cmd=("${compose[@]}" exec -T postgres pg_restore --no-owner --no-acl
  --role="$POSTGRES_USER" --single-transaction --exit-on-error
  -U "$POSTGRES_USER" -d "$POSTGRES_DB")

if [[ "$dump_file" == *.age ]]; then
  set +e
  age -d -i "$identity" "$dump_file" | "${restore_cmd[@]}"
  statuses=("${PIPESTATUS[@]}")
  set -e
  if [ "${statuses[0]}" -ne 0 ] || [ "${statuses[1]}" -ne 0 ]; then
    echo "restore: failed (age ${statuses[0]}, pg_restore ${statuses[1]})" >&2
    exit 1
  fi
else
  "${restore_cmd[@]}" < "$dump_file"
fi

echo "restore: done. api and collector are still stopped."
if [ "$IMAGE_TAG" != none ]; then
  echo "Next: IMAGE_TAG=${IMAGE_TAG} ~/harvester/deploy.sh migrate caddy api collector"
else
  echo "Next: run the Release workflow by hand with services 'migrate caddy api collector'."
fi
if [ -n "$identity" ]; then
  echo "Delete the identity file now: rm ${identity}"
fi
