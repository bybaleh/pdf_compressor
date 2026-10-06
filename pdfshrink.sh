#!/usr/bin/env bash
# Удобная обёртка: собирает образ при необходимости и запускает разовый контейнер.
#
#   ./pdfshrink.sh report.pdf
#   ./pdfshrink.sh report.pdf --target-size 5MB -v
#
# Текущий каталог монтируется в /data внутри контейнера,
# контейнер удаляется сразу после работы (--rm).
set -euo pipefail

IMAGE="${PDFSHRINK_IMAGE:-pdfshrink:latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo ">> Образ $IMAGE не найден, собираю..." >&2
  docker build -t "$IMAGE" "$SCRIPT_DIR"
fi

exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD:/data" \
  --workdir /data \
  "$IMAGE" "$@"
