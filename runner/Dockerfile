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

# ─── 1. 基础工具 + Docker 官方仓库（含 docker-compose-plugin v2） ──────
# Debian/Ubuntu 自带 docker.io 是 20.10，缺 docker-compose-plugin。
# 走官方源装 docker-ce + compose plugin + buildx。
RUN install -m 0755 -d /etc/apt/keyrings \
    && apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates gnupg curl iptables \
        git gh jq make bash sudo nodejs npm ssh-client \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin \
        fuse-overlayfs \
    && rm -rf /var/lib/apt/lists/*

# ─── 2. Go 1.22 ─────────────────────────────────────────────────────────
ARG GO_VERSION=1.23.4
RUN curl -fsSL https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz \
      | tar -xz -C /usr/local
ENV PATH="/usr/local/go/bin:/root/go/bin:$PATH" \
    GOPATH=/root/go

# ─── 3. openspec CLI ─────────────────────────────────────────────────────
# 真包名是 @fission-ai/openspec（旧 Dockerfile 装的 @fission-codes/openspec 不存在，
# 退回 openspec 是 npm 上的占位空包，REQ-997 analyze 暴露的）
RUN npm install -g @fission-ai/openspec@latest && openspec --version

# ─── 4. sisyphus 合约脚本 + v0.2 manifest validator ──────────────────
# build context 必须是 sisyphus repo 根（CI workflow 已配 context: .）
# validator 要 PyYAML
RUN pip3 install --break-system-packages pyyaml 2>/dev/null || pip3 install pyyaml
COPY scripts/check-scenario-refs.sh \
     scripts/check-tasks-section-ownership.sh \
     scripts/pre-commit-acl.sh \
     scripts/sisyphus-clone-repos.sh \
     scripts/validate-manifest.py \
     /opt/sisyphus/scripts/
RUN chmod +x /opt/sisyphus/scripts/*.sh /opt/sisyphus/scripts/*.py
ENV PATH="/opt/sisyphus/scripts:$PATH"

# ─── 5. DinD 入口 ─────────────────────────────────────────────────────────
COPY runner/entrypoint.sh /usr/local/bin/sisyphus-entrypoint.sh
RUN chmod +x /usr/local/bin/sisyphus-entrypoint.sh

WORKDIR /workspace
ENV DOCKER_HOST="unix:///var/run/docker.sock" \
    SISYPHUS_RUNNER=1

ENTRYPOINT ["/usr/local/bin/sisyphus-entrypoint.sh"]
CMD ["sleep", "infinity"]
