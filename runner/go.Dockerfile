# sisyphus-runner-go: Go 项目专用轻量 runner（~1GB）
#
# 跟 runner/Dockerfile（Flutter，~5GB）平行存在。Go 项目（如 ubox-crosser）用
# 这个，不必带 Flutter SDK / Android SDK。Flutter 项目仍用 sisyphus-runner:main。
#
# 用法跟 sisyphus-runner 完全相同（runner_container.md.j2 通过 image 字段切）。
FROM golang:1.22-bookworm

LABEL org.opencontainers.image.source=https://github.com/phona/sisyphus
LABEL org.opencontainers.image.description="Sisyphus Go-only runner: Go + Docker DinD + openspec + sisyphus tools"
LABEL org.opencontainers.image.licenses=MIT

# ─── 1. 基础工具 + dockerd（DinD） ──────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        docker.io iptables \
        git curl jq make bash sudo \
        ca-certificates gnupg \
        nodejs npm \
        ssh-client \
    && rm -rf /var/lib/apt/lists/*

# gh CLI（apt 仓库装）
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# ─── 2. openspec CLI ─────────────────────────────────────────────────────
RUN npm install -g @fission-codes/openspec 2>/dev/null || \
    npm install -g openspec 2>/dev/null || \
    echo "openspec install probe — adjust pkg name if needed"

# ─── 3. sisyphus 合约脚本 ───────────────────────────────────────────────
COPY scripts/check-scenario-refs.sh \
     scripts/check-tasks-section-ownership.sh \
     scripts/pre-commit-acl.sh \
     /opt/sisyphus/scripts/
RUN chmod +x /opt/sisyphus/scripts/*.sh
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
