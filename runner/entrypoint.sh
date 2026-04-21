#!/usr/bin/env bash
# sisyphus-runner 入口：起 dockerd（DinD）+ exec 主命令。
#
# DinD 起动条件：容器须以 privileged 跑，/var/lib/docker 可写
# 不需要 docker 时传环境变量 SISYPHUS_NO_DOCKER=1 跳过
set -euo pipefail

if [[ "${SISYPHUS_NO_DOCKER:-0}" != "1" ]]; then
  if [[ ! -S /var/run/docker.sock ]]; then
    echo "[sisyphus-entrypoint] starting dockerd in background..."
    # 在 K3s/K8s 嵌套环境里 overlay2 不可用（双层 overlayfs），用 vfs 兜底
    # vfs 性能差但 nested 安全。要更快可装 fuse-overlayfs 包并改成它。
    nohup dockerd \
      --host=unix:///var/run/docker.sock \
      --storage-driver=vfs \
      > /var/log/dockerd.log 2>&1 &
    # 等 socket（最多 30s）
    for _ in $(seq 1 30); do
      [[ -S /var/run/docker.sock ]] && break
      sleep 1
    done
    if [[ -S /var/run/docker.sock ]]; then
      echo "[sisyphus-entrypoint] dockerd ready"
    else
      echo "[sisyphus-entrypoint] dockerd FAILED — check /var/log/dockerd.log" >&2
    fi
  fi
fi

exec "$@"
