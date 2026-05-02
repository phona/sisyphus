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

# ── Git ambient auth (#286): GH_TOKEN → ~/.netrc ──────────────────────
# Helm 把 runner secret 里的 gh_token 注成 GH_TOKEN env（contract 见 helm
# values L137-145：「**只供 runner 内 git clone 私有仓**」）。但 vanilla
# `git clone https://github.com/...` 不会主动读 env，业务仓 Makefile 里写
# `git clone https://github.com/<private-repo>` 形式（accept-env-up 跨仓
# clone lab repo 是常见用法）就会撞 `could not read Username for
# 'https://github.com'` 死翘翘。
# 把 GH_TOKEN 落到 ~/.netrc 给 git ambient auth；GitHub 接受任意 username + PAT
# 作 password，所以 login=oauth2 是惯例占位（也能换成 token-with-x-access-token，
# 都行）。pod restart 重跑 entrypoint 自动重写，无残留 / 无 PVC 干扰
# （写在 / 不在 /workspace）。
if [[ -n "${GH_TOKEN:-}" ]]; then
  cat > /root/.netrc <<EOF
machine github.com
login oauth2
password ${GH_TOKEN}
machine api.github.com
login oauth2
password ${GH_TOKEN}
EOF
  chmod 600 /root/.netrc
  echo "[sisyphus-entrypoint] git ambient auth configured via ~/.netrc"
else
  echo "[sisyphus-entrypoint] GH_TOKEN missing — git private-repo clone will fail" >&2
fi

# ── Workspace 目录契约 + restart 恢复 ──────────────────────────────────
# Pod restart（restartPolicy=Always）时，entrypoint.sh 会重新跑。
# 用 /.sisyphus-runner-init 标记检测 restart：标记存在 → 清掉 /workspace/* 从零开始。
INIT_MARKER="/.sisyphus-runner-init"
WORKSPACE="/workspace"

mkdir -p "$WORKSPACE"

if [[ -f "$INIT_MARKER" ]]; then
  echo "[sisyphus-entrypoint] restart detected (init marker present), cleaning workspace residue..."
  rm -rf "$WORKSPACE"/* "$WORKSPACE"/.[!.]* 2>/dev/null || true
fi

# 确保标准目录存在
mkdir -p "$WORKSPACE/source"
mkdir -p "$WORKSPACE/integration"

# 目录结构校验：/workspace/source 非空时，每个子项必须是合法 git repo。
# 结构异常（PVC 残留 / 前一个 REQ 的脏数据）→ 清掉重来。
if [[ -d "$WORKSPACE/source" ]]; then
  malformed=0
  for entry in "$WORKSPACE/source"/*; do
    [[ -e "$entry" ]] || continue
    if [[ ! -d "$entry" ]]; then
      echo "[sisyphus-entrypoint] malformed: $entry is not a directory" >&2
      malformed=1
    elif [[ ! -d "$entry/.git" ]]; then
      echo "[sisyphus-entrypoint] malformed: $entry is not a git repo" >&2
      malformed=1
    fi
  done
  if [[ "$malformed" -eq 1 ]]; then
    echo "[sisyphus-entrypoint] cleaning malformed /workspace/source/*" >&2
    rm -rf "$WORKSPACE/source"/*
  fi
fi

# 写入初始化标记
touch "$INIT_MARKER"

exec "$@"
