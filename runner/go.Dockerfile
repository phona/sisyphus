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

# ─── 2. openspec CLI ─────────────────────────────────────────────────────
# 真包名是 @fission-ai/openspec（旧版本误装 npm 上的占位 openspec@0.0.0 是空的，导致
# REQ-997 analyze 报 "openspec: command not found"）
RUN npm install -g @fission-ai/openspec@latest && openspec --version

# ─── 3. sisyphus 合约脚本 + v0.2 manifest validator ──────────────────
# validator 要 PyYAML
RUN pip3 install --break-system-packages pyyaml
COPY scripts/check-scenario-refs.sh \
     scripts/check-tasks-section-ownership.sh \
     scripts/pre-commit-acl.sh \
     scripts/validate-manifest.py \
     /opt/sisyphus/scripts/
RUN chmod +x /opt/sisyphus/scripts/*.sh /opt/sisyphus/scripts/*.py
ENV PATH="/opt/sisyphus/scripts:$PATH"

# ─── 4. DinD 入口（跟 runner/entrypoint.sh 共用） ──────────────────────
COPY runner/entrypoint.sh /usr/local/bin/sisyphus-entrypoint.sh
RUN chmod +x /usr/local/bin/sisyphus-entrypoint.sh

WORKDIR /workspace
ENV DOCKER_HOST="unix:///var/run/docker.sock" \
    SISYPHUS_RUNNER=1 \
    GOPATH=/root/go \
    PATH="/root/go/bin:$PATH"

ENTRYPOINT ["/usr/local/bin/sisyphus-entrypoint.sh"]
CMD ["sleep", "infinity"]
