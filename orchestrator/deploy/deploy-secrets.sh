#!/usr/bin/env bash
# Sisyphus v0.2 secret 部署脚本（vm-node04 上跑）
# ─────────────────────────────────────────────────────────────────
# 两种模式：
#
#   1) 交互式（在 vm-node04 本地直接跑；read -s 输入密码不落 history）
#        bash deploy-secrets.sh
#
#   2) 从 env 文件读（通过 sisyphus 部署方的 file_deploy 送到 /tmp 后跑）
#        bash deploy-secrets.sh --env-file /tmp/secrets.env
#
# 不管哪种模式，都做：
#   - 建 ns: sisyphus, sisyphus-runners
#   - 建 secret orch-sisyphus-orchestrator (bkd_token + webhook_token)
#   - 建 secret sisyphus-runner-secrets (gh_token + ghcr_user + ghcr_token + kubeconfig)
#
# 跑完删 --env-file 指向的文件（防敏感信息残留）。
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

ENV_FILE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file) ENV_FILE="$2"; shift 2 ;;
        *) echo "unknown arg: $1"; exit 2 ;;
    esac
done

echo "=== Sisyphus v0.2 secret 部署 ==="
command -v kubectl >/dev/null || { echo "FATAL: 没找到 kubectl"; exit 1; }

# ── 1. namespaces ───────────────────────────────────────────────
for ns in sisyphus sisyphus-runners; do
    kubectl get ns "$ns" >/dev/null 2>&1 || kubectl create namespace "$ns"
done

# ── 2. 从 env 文件或交互式收集值 ─────────────────────────────────
if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -r "$ENV_FILE" ]]; then
        echo "FATAL: --env-file $ENV_FILE 不可读"
        exit 1
    fi
    # shellcheck disable=SC1090
    set -a
    source "$ENV_FILE"
    set +a
else
    # 交互式
    read -srp "BKD token: " SISYPHUS_BKD_TOKEN; echo
    read -srp "webhook token（空=自动生成）: " SISYPHUS_WEBHOOK_TOKEN; echo
    read -srp "GH Fine-grained PAT: " SISYPHUS_GH_TOKEN; echo
    read -rp  "GHCR 用户名: " SISYPHUS_GHCR_USER
    read -srp "GHCR Classic PAT (read:packages): " SISYPHUS_GHCR_TOKEN; echo
    read -rp  "kubeconfig 路径 [/etc/rancher/k3s/k3s.yaml]: " SISYPHUS_KUBECONFIG_PATH
    SISYPHUS_KUBECONFIG_PATH="${SISYPHUS_KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}"
fi

# 兜底生成 webhook_token
if [[ -z "${SISYPHUS_WEBHOOK_TOKEN:-}" ]]; then
    SISYPHUS_WEBHOOK_TOKEN=$(openssl rand -hex 32)
    echo
    echo "[info] 自动生成 webhook_token（BKD webhook 配置要填这个；记下来）："
    echo "    $SISYPHUS_WEBHOOK_TOKEN"
    echo
fi

# 必填校验
for v in SISYPHUS_BKD_TOKEN SISYPHUS_GH_TOKEN SISYPHUS_GHCR_USER SISYPHUS_GHCR_TOKEN; do
    if [[ -z "${!v:-}" ]]; then
        echo "FATAL: $v 未填"
        exit 1
    fi
done

# ── 3. kubeconfig 路径（可能需要 sudo）────────────────────────────
KUBECONFIG_SRC="${SISYPHUS_KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}"
CLEANUP_KUBECONFIG=0
if [[ ! -r "$KUBECONFIG_SRC" ]]; then
    echo "[info] $KUBECONFIG_SRC 不可读，sudo 复制到 /tmp"
    sudo cat "$KUBECONFIG_SRC" > /tmp/sisyphus-kubeconfig.tmp
    chmod 600 /tmp/sisyphus-kubeconfig.tmp
    KUBECONFIG_SRC=/tmp/sisyphus-kubeconfig.tmp
    CLEANUP_KUBECONFIG=1
fi

# ── 4. 建 secret ────────────────────────────────────────────────
kubectl -n sisyphus create secret generic orch-sisyphus-orchestrator \
    --from-literal=bkd_token="$SISYPHUS_BKD_TOKEN" \
    --from-literal=webhook_token="$SISYPHUS_WEBHOOK_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -

kubectl -n sisyphus-runners create secret generic sisyphus-runner-secrets \
    --from-literal=gh_token="$SISYPHUS_GH_TOKEN" \
    --from-literal=ghcr_user="$SISYPHUS_GHCR_USER" \
    --from-literal=ghcr_token="$SISYPHUS_GHCR_TOKEN" \
    --from-file=kubeconfig="$KUBECONFIG_SRC" \
    --dry-run=client -o yaml | kubectl apply -f -

# ── 5. 清理临时敏感文件 ──────────────────────────────────────────
[[ "$CLEANUP_KUBECONFIG" == 1 ]] && rm -f /tmp/sisyphus-kubeconfig.tmp
if [[ -n "$ENV_FILE" ]]; then
    shred -u "$ENV_FILE" 2>/dev/null || rm -f "$ENV_FILE"
    echo "[info] 已清理 $ENV_FILE"
fi

# ── 6. 验证 ────────────────────────────────────────────────────
echo
echo "── 核对结果 ──"
for row in "sisyphus orch-sisyphus-orchestrator" "sisyphus-runners sisyphus-runner-secrets"; do
    set -- $row
    ns="$1"; name="$2"
    if kubectl -n "$ns" get secret "$name" >/dev/null 2>&1; then
        keys=$(kubectl -n "$ns" get secret "$name" -o jsonpath='{.data}' | \
               python3 -c 'import sys,json; print(",".join(json.loads(sys.stdin.read()).keys()))')
        echo "  ✓ $ns / $name  keys: $keys"
    else
        echo "  ✗ $ns / $name  MISSING" >&2
        exit 1
    fi
done

echo
echo "=== 完成 ==="
if [[ -n "$ENV_FILE" ]] && [[ -n "${SISYPHUS_WEBHOOK_TOKEN_GENERATED:-}" ]]; then
    echo "记得把 webhook_token 配到 BKD webhook"
fi
