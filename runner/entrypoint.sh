#!/usr/bin/env bash
# sisyphus-runner 入口：起 dockerd（DinD）+ exec 主命令。
#
# DinD 起动条件：容器须以 privileged 跑，/var/lib/docker 可写
# 不需要 docker 时传环境变量 SISYPHUS_NO_DOCKER=1 跳过
set -euo pipefail

if [[ "${SISYPHUS_NO_DOCKER:-0}" != "1" ]]; then
  if [[ ! -S /var/run/docker.sock ]]; then
    echo "[sisyphus-entrypoint] starting dockerd in background..."
    # 嵌套 overlayfs 内核不允许叠两层 → 用 fuse-overlayfs（用户态实现，3-5x 省空间 vs vfs）
    # 前提：宿主有 /dev/fuse，docker run 时 --device /dev/fuse 挂进来
    # 没有 /dev/fuse 时自动退回 vfs（容灾）
    if [[ -c /dev/fuse ]]; then
      DRIVER=fuse-overlayfs
    else
      echo "[sisyphus-entrypoint] /dev/fuse missing, falling back to vfs (slow, 10x disk)" >&2
      DRIVER=vfs
    fi
    nohup dockerd \
      --host=unix:///var/run/docker.sock \
      --storage-driver=$DRIVER \
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
