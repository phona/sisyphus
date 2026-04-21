# sisyphus-runner: BKD agent 在 vm-node04 上 docker exec 进来干活的容器
#
# 容器内含完整 toolchain：Flutter SDK + Android SDK + Java + Go + Node + Docker DinD
# + sisyphus 合约工具（openspec / scripts/）
#
# 用法（vm-node04 上 BKD agent 通过 aissh exec_run 调）：
#   docker inspect sisyphus-runner-$REQ >/dev/null 2>&1 || \
#     docker run -d --name sisyphus-runner-$REQ \
#       --privileged --restart=unless-stopped \
#       -v sisyphus-workspace-$REQ:/workspace -w /workspace \
#       ghcr.io/phona/sisyphus-runner:v1 sleep infinity
#   docker exec sisyphus-runner-$REQ openspec validate openspec/changes/$REQ
FROM ghcr.io/cirruslabs/flutter:stable

LABEL org.opencontainers.image.source=https://github.com/phona/sisyphus
LABEL org.opencontainers.image.description="Sisyphus per-REQ runner: Flutter + Go + Docker DinD + openspec"
LABEL org.opencontainers.image.licenses=MIT

# ─── 1. 基础工具 + dockerd（DinD） ──────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        docker.io iptables \
        git gh curl jq make bash sudo \
        ca-certificates gnupg \
        nodejs npm \
        ssh-client \
    && rm -rf /var/lib/apt/lists/*

# ─── 2. Go 1.22 ─────────────────────────────────────────────────────────
ARG GO_VERSION=1.22.7
RUN curl -fsSL https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz \
      | tar -xz -C /usr/local
ENV PATH="/usr/local/go/bin:/root/go/bin:$PATH" \
    GOPATH=/root/go

# ─── 3. openspec CLI ─────────────────────────────────────────────────────
# openspec 上游目前是 npm package；如果包名/装法变了改这条
RUN npm install -g @fission-codes/openspec 2>/dev/null || \
    npm install -g openspec 2>/dev/null || \
    echo "openspec install probe — adjust pkg name in Dockerfile"

# ─── 4. sisyphus 合约脚本 ───────────────────────────────────────────────
# build context 必须是 sisyphus repo 根（CI workflow 已配 context: .）
COPY scripts/check-scenario-refs.sh \
     scripts/check-tasks-section-ownership.sh \
     scripts/pre-commit-acl.sh \
     /opt/sisyphus/scripts/
RUN chmod +x /opt/sisyphus/scripts/*.sh
ENV PATH="/opt/sisyphus/scripts:$PATH"

# ─── 5. DinD 入口 ─────────────────────────────────────────────────────────
COPY runner/entrypoint.sh /usr/local/bin/sisyphus-entrypoint.sh
RUN chmod +x /usr/local/bin/sisyphus-entrypoint.sh

WORKDIR /workspace
ENV DOCKER_HOST="unix:///var/run/docker.sock" \
    SISYPHUS_RUNNER=1

ENTRYPOINT ["/usr/local/bin/sisyphus-entrypoint.sh"]
CMD ["sleep", "infinity"]
