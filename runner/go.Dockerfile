# sisyphus-runner-go: Go 项目专用轻量 runner（~1GB）
#
# 跟 runner/Dockerfile（Flutter，~5GB）平行存在。Go 项目（如 ubox-crosser）用
# 这个，不必带 Flutter SDK / Android SDK。Flutter 项目仍用 sisyphus-runner:main。
#
# 用法跟 sisyphus-runner 完全相同（runner_container.md.j2 通过 image 字段切）。
FROM golang:1.23-bookworm

LABEL org.opencontainers.image.source=https://github.com/phona/sisyphus
LABEL org.opencontainers.image.description="Sisyphus Go-only runner: Go + Docker DinD + openspec + sisyphus tools"
LABEL org.opencontainers.image.licenses=MIT

# ─── 1. 基础工具 + Docker 官方仓库（DinD + compose v2 plugin） ──────────
# Debian 自带的 docker.io 是 20.10，缺 docker-compose-plugin（v2 syntax `docker compose` 不可用）。
# 走官方仓库装 docker-ce + docker-compose-plugin + buildx，跟生产 CI 对齐。
RUN install -m 0755 -d /etc/apt/keyrings \
    && apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates gnupg curl iptables \
        git jq make bash sudo nodejs npm ssh-client \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin \
        fuse-overlayfs \
    && rm -rf /var/lib/apt/lists/*

# gh CLI（apt 仓库装）
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# ─── 2. helm ─────────────────────────────────────────────────────────────
ARG HELM_VERSION=3.17.3
RUN curl -fsSL https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz \
      | tar -xz --strip-components=1 -C /usr/local/bin linux-amd64/helm \
    && helm version

# ─── 3. openspec CLI ─────────────────────────────────────────────────────
# 真包名是 @fission-ai/openspec（旧版本误装 npm 上的占位 openspec@0.0.0 是空的，导致
# REQ-997 analyze 报 "openspec: command not found"）
RUN npm install -g @fission-ai/openspec@latest && openspec --version

# ─── 3b. golangci-lint（业务仓 Makefile ci-lint target 依赖） ──────────
# REQ-final13 实证：ubox-crosser dev-cross-check → ci-lint 调 golangci-lint，没装
# 时 fixer 死循环试图修 Makefile 也修不好。Go runner image 装上避免 env-bug 转 fix-dev。
RUN curl -sSfL https://raw.githubusercontent.com/golangci/golangci-lint/HEAD/install.sh \
    | sh -s -- -b /usr/local/bin v1.62.2 \
    && golangci-lint --version

# ─── 3c. uv（sisyphus 自 dogfood + 任何 Python 业务仓 Makefile 用 uv run） ──────
# 实证 2026-04-26 REQ-impl-gh-incident-open-1777173133 dev_cross_check stderr：
#   /bin/sh: 12: uv: not found
#   make: *** [Makefile:27: ci-lint] Error 127
# sisyphus 自身 Makefile ci-lint 跑 `cd orchestrator && uv run ruff check`，runner
# 镜像没 uv → 所有 sisyphus 自 dogfood 在 dev_cross_check 必失败。Python 业务仓也常
# 用 uv（Astral 推 Python 包管），同步装上避免重蹈 golangci-lint 覆辙。
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && uv --version

# ─── 4. sisyphus 合约脚本 ──────────────────
COPY scripts/check-scenario-refs.sh \
     scripts/check-tasks-section-ownership.sh \
     scripts/pre-commit-acl.sh \
     scripts/sisyphus-clone-repos.sh \
     /opt/sisyphus/scripts/
RUN chmod +x /opt/sisyphus/scripts/*.sh
ENV PATH="/opt/sisyphus/scripts:$PATH"

# ─── 5. DinD 入口（跟 runner/entrypoint.sh 共用） ──────────────────────
COPY runner/entrypoint.sh /usr/local/bin/sisyphus-entrypoint.sh
RUN chmod +x /usr/local/bin/sisyphus-entrypoint.sh

WORKDIR /workspace
ENV DOCKER_HOST="unix:///var/run/docker.sock" \
    SISYPHUS_RUNNER=1 \
    GOPATH=/root/go \
    PATH="/root/go/bin:$PATH"

ENTRYPOINT ["/usr/local/bin/sisyphus-entrypoint.sh"]
CMD ["sleep", "infinity"]
