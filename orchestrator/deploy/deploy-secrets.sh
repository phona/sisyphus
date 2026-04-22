#!/usr/bin/env bash
# Sisyphus v0.2 secret 注入脚本（在 vm-node04 上跑一次即可）
# ─────────────────────────────────────────────────────────────────
# 作用：
#   1. 确保 sisyphus / sisyphus-runners namespace 存在
#   2. 创建 orchestrator secret（bkd_token + webhook_token + pg_dsn）
#   3. 创建 runner secret（gh_token + ghcr_user + ghcr_token + kubeconfig）
#
# 所有 secret 值**交互式输入**（`read -s`），不落 shell history，不打 log。
#
# 用法：
#   ssh vm-node04
#   cd <sisyphus repo>/orchestrator/deploy
#   bash deploy-secrets.sh
#
# 重跑幂等：已存在的 secret 会被 `kubectl create --dry-run | kubectl apply -f -` 更新。
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

echo "=== Sisyphus v0.2 secret 部署 ==="
echo

# ── 0. 预检 ─────────────────────────────────────────────────────
command -v kubectl >/dev/null || { echo "FATAL: 没找到 kubectl"; exit 1; }

# ── 1. namespace ────────────────────────────────────────────────
for ns in sisyphus sisyphus-runners; do
    if ! kubectl get ns "$ns" >/dev/null 2>&1; then
        echo "[info] create ns/$ns"
        kubectl create namespace "$ns"
    fi
done

# ── 2. orchestrator secret (ns: sisyphus) ────────────────────────
echo
echo "── 2. Orchestrator secret (ns: sisyphus / name: orch-sisyphus-orchestrator) ──"
read -srp "BKD token (Coder-Session-Token): " BKD_TOKEN; echo
read -srp "webhook token (自家发；建议 openssl rand -hex 32；回车自动生成): " WEBHOOK_TOKEN; echo
if [[ -z "$WEBHOOK_TOKEN" ]]; then
    WEBHOOK_TOKEN=$(openssl rand -hex 32)
    echo "[info] 已生成 webhook_token（记下来给 BKD webhook 配置用）："
    echo "    $WEBHOOK_TOKEN"
fi

kubectl -n sisyphus create secret generic orch-sisyphus-orchestrator \
    --from-literal=bkd_token="$BKD_TOKEN" \
    --from-literal=webhook_token="$WEBHOOK_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -

# ── 3. runner secret (ns: sisyphus-runners) ─────────────────────
echo
echo "── 3. Runner secret (ns: sisyphus-runners / name: sisyphus-runner-secrets) ──"
read -srp "GH Fine-grained PAT (gh_token, Contents Read + Commit statuses Read): " GH_TOKEN; echo
read -rp  "GHCR 登录用户名 (ghcr_user, 比如 sisyphus-bot): " GHCR_USER
read -srp "GHCR Classic PAT (ghcr_token, 只 read:packages): " GHCR_TOKEN; echo

KUBECONFIG_PATH=""
read -rp "kubeconfig 文件路径 (给 runner Pod 内 agent 起 helm 用；默认 /etc/rancher/k3s/k3s.yaml): " KUBECONFIG_PATH
KUBECONFIG_PATH="${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}"
if [[ ! -r "$KUBECONFIG_PATH" ]]; then
    echo "[warn] $KUBECONFIG_PATH 不可读 — 用 sudo cat 绕一下"
    sudo cat "$KUBECONFIG_PATH" > /tmp/sisyphus-kubeconfig.tmp
    KUBECONFIG_PATH=/tmp/sisyphus-kubeconfig.tmp
    cleanup_kubeconfig=1
fi

kubectl -n sisyphus-runners create secret generic sisyphus-runner-secrets \
    --from-literal=gh_token="$GH_TOKEN" \
    --from-literal=ghcr_user="$GHCR_USER" \
    --from-literal=ghcr_token="$GHCR_TOKEN" \
    --from-file=kubeconfig="$KUBECONFIG_PATH" \
    --dry-run=client -o yaml | kubectl apply -f -

[[ "${cleanup_kubeconfig:-0}" == 1 ]] && rm -f /tmp/sisyphus-kubeconfig.tmp

# ── 4. 验证 ─────────────────────────────────────────────────────
echo
echo "── 4. 核对结果 ──"
for row in "sisyphus orch-sisyphus-orchestrator" "sisyphus-runners sisyphus-runner-secrets"; do
    set -- $row
    ns="$1"; name="$2"
    if kubectl -n "$ns" get secret "$name" >/dev/null 2>&1; then
        keys=$(kubectl -n "$ns" get secret "$name" -o jsonpath='{.data}' | \
               python3 -c 'import sys,json; print(",".join(json.loads(sys.stdin.read()).keys()))')
        echo "  ✓ $ns / $name  keys: $keys"
    else
        echo "  ✗ $ns / $name  MISSING"
    fi
done

echo
echo "=== 完成 ==="
echo
echo "下一步："
echo "  1. webhook_token 记下来（给 BKD webhook 配置用）"
echo "  2. 通知 sisyphus 部署方，跑:"
echo "     helm -n sisyphus upgrade --install orch ./orchestrator/helm -f deploy/my-values.yaml"
