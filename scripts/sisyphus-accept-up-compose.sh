#!/usr/bin/env bash
# sisyphus-accept-up-compose.sh — sisyphus self-dogfood accept env-up（REQ-self-accept-stage-1777121797）
#
# 在 runner pod 的 DinD 里跑：build orchestrator image + compose up postgres + orchestrator，
# 等 /healthz 返 200，然后 stdout **末行** emit endpoint JSON 给 create_accept.py 解析。
#
# 必须从 sisyphus 仓 root 调（`cd /workspace/source/sisyphus && make accept-env-up`）
# 因为 deploy/accept-compose.yml 的 build context 是相对路径 `../orchestrator`。
#
# 环境变量：
#   SISYPHUS_NAMESPACE   - compose project name (orchestrator 注入；默认 accept-default)
#   SISYPHUS_ACCEPT_PORT - 暴露给 host 的端口 (默认 18000)
#   SISYPHUS_REQ_ID      - REQ id（仅 logging 用）

set -euo pipefail

NAMESPACE="${SISYPHUS_NAMESPACE:-accept-default}"
PORT="${SISYPHUS_ACCEPT_PORT:-18000}"
REQ_ID="${SISYPHUS_REQ_ID:-unknown}"

COMPOSE_FILE="$(cd "$(dirname "$0")/.." && pwd)/deploy/accept-compose.yml"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "=== FAIL accept-up: $COMPOSE_FILE not found ===" >&2
  exit 1
fi

# stderr：所有进度 log；stdout：仅末行 JSON（合契约）
log() { echo "[accept-up $REQ_ID]" "$@" >&2; }

# 兜底清掉同 namespace 旧栈（idempotency；防上次 run 残留）
log "tearing down stale stack (if any) for project=$NAMESPACE"
docker compose -p "$NAMESPACE" -f "$COMPOSE_FILE" down -v --remove-orphans >&2 2>&1 || true

log "building + starting compose stack (project=$NAMESPACE port=$PORT)"
SISYPHUS_ACCEPT_PORT="$PORT" \
  docker compose -p "$NAMESPACE" -f "$COMPOSE_FILE" up -d --build --wait --wait-timeout 180 >&2

# --wait 已等到 healthcheck pass，但再用外部 curl 兜一次（确认 host port 真通）
log "verifying http://localhost:$PORT/healthz"
for i in $(seq 1 30); do
  if curl -sf -m 3 "http://localhost:$PORT/healthz" >/dev/null 2>&1; then
    log "healthz OK after ${i} attempt(s)"
    break
  fi
  if [[ $i -eq 30 ]]; then
    log "healthz never became reachable; dumping orchestrator logs"
    docker compose -p "$NAMESPACE" -f "$COMPOSE_FILE" logs --tail=80 orchestrator >&2 || true
    echo "=== FAIL accept-up: /healthz unreachable on port $PORT ===" >&2
    exit 2
  fi
  sleep 1
done

# 末行 JSON（create_accept.py 反向取 splitlines() 第一个非空行）
printf '{"endpoint":"http://localhost:%s","namespace":"%s"}\n' "$PORT" "$NAMESPACE"
