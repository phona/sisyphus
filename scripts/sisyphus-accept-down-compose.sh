#!/usr/bin/env bash
# sisyphus-accept-down-compose.sh — sisyphus self-dogfood accept env-down（幂等）
#
# 由顶层 Makefile ci-accept-env-down target 调。always exit 0 — best-effort cleanup
# 不阻塞状态机（teardown_accept_env.py 也是 best-effort 语义）。
#
# 环境变量：
#   SISYPHUS_NAMESPACE   - compose project name（默认 accept-default）

set -euo pipefail

NAMESPACE="${SISYPHUS_NAMESPACE:-accept-default}"
REQ_ID="${SISYPHUS_REQ_ID:-unknown}"

COMPOSE_FILE="$(cd "$(dirname "$0")/.." && pwd)/deploy/accept-compose.yml"

log() { echo "[accept-down $REQ_ID]" "$@" >&2; }

if [[ ! -f "$COMPOSE_FILE" ]]; then
  log "compose file not found at $COMPOSE_FILE — assuming already torn down"
  exit 0
fi

log "tearing down compose stack (project=$NAMESPACE)"
# down -v 删 anonymous volumes（postgres 数据是 anon 的，每次干净）；--remove-orphans
# 防新增/重命名服务后留下孤儿容器
docker compose -p "$NAMESPACE" -f "$COMPOSE_FILE" down -v --remove-orphans >&2 2>&1 || {
  log "compose down returned non-zero; treating as best-effort success"
}

log "done"
exit 0
