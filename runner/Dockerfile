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

# ─── 1. 基础工具 + Docker 官方仓库（含 docker-compose-plugin v2） + KVM userland ──
# Debian/Ubuntu 自带 docker.io 是 20.10，缺 docker-compose-plugin。
# 走官方源装 docker-ce + compose plugin + buildx。
# 同步装 qemu-kvm + cpu-checker：Android emulator 在 KVM 加速下冷启 ~30s（vs 软 CPU
# 5-10min），需要宿主 /dev/kvm 挂入容器（k8s_runner.py kvm_enabled=true 启用）。
# cpu-checker 提供 kvm-ok 用于 emulator 启动前自检。
RUN install -m 0755 -d /etc/apt/keyrings \
    && apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates gnupg curl iptables \
        git gh jq make bash sudo nodejs npm ssh-client \
        qemu-kvm cpu-checker \
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

# ─── 2. helm ─────────────────────────────────────────────────────────────
ARG HELM_VERSION=3.17.3
RUN curl -fsSL https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz \
      | tar -xz --strip-components=1 -C /usr/local/bin linux-amd64/helm \
    && helm version

# ─── 3. Go 1.23 ─────────────────────────────────────────────────────────
ARG GO_VERSION=1.23.4
RUN curl -fsSL https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz \
      | tar -xz -C /usr/local
ENV PATH="/usr/local/go/bin:/root/go/bin:$PATH" \
    GOPATH=/root/go

# ─── 4. openspec CLI ─────────────────────────────────────────────────────
# 真包名是 @fission-ai/openspec（旧 Dockerfile 装的 @fission-codes/openspec 不存在，
# 退回 openspec 是 npm 上的占位空包，REQ-997 analyze 暴露的）
RUN npm install -g @fission-ai/openspec@latest && openspec --version

# ─── 4b. uv（sisyphus 自 dogfood + Python 业务仓 Makefile 用 uv run 必装） ─────
# 实证 2026-04-26 REQ-impl-gh-incident-open-1777173133 dev_cross_check stderr：
#   /bin/sh: 12: uv: not found
#   make: *** [Makefile:27: ci-lint] Error 127
# sisyphus Makefile ci-lint 走 `cd orchestrator && uv run ruff check`，runner 镜像
# 没 uv → 所有 sisyphus 自 dogfood 在 dev_cross_check 必失败。Flutter runner 也加上
# 跟 Go runner 对齐（避免 ttpos-flutter REQ 撞同款）。
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && uv --version

# ─── 4c. Android emulator + system image + 默认 AVD ──────────────────────
# Cirrus flutter base 已带 Android SDK + sdkmanager + platform-tools (adb)，但默认
# 没装 emulator package 也没装 system-image。这里补齐：
#   - emulator (QEMU 包装)
#   - system-images;android-34;google_apis;x86_64 (~1GB)
#   - 预创建 AVD `sisyphus-default` (跳过运行时 ~10s 部署延迟)
# 选型理由见 openspec/changes/REQ-runner-android-emulator-1777189159/design.md §2/§4。
# yes | sdkmanager 自动同意 license；ANDROID_SDK_ROOT 由 cirrus base 设好。
ARG ANDROID_API_LEVEL=34
ARG ANDROID_SYSTEM_IMAGE=system-images;android-34;google_apis;x86_64
RUN yes | sdkmanager --install \
        "emulator" \
        "${ANDROID_SYSTEM_IMAGE}" \
    && echo "no" | avdmanager create avd \
        --name sisyphus-default \
        --package "${ANDROID_SYSTEM_IMAGE}" \
        --device "pixel" \
        --force \
    && emulator -list-avds | grep -q sisyphus-default

# ─── 5. sisyphus 合约脚本 ──────────────────
# build context 必须是 sisyphus repo 根（CI workflow 已配 context: .）
COPY scripts/check-scenario-refs.sh \
     scripts/check-tasks-section-ownership.sh \
     scripts/pre-commit-acl.sh \
     scripts/sisyphus-clone-repos.sh \
     scripts/sisyphus-android-emulator.sh \
     /opt/sisyphus/scripts/
RUN chmod +x /opt/sisyphus/scripts/*.sh
ENV PATH="/opt/sisyphus/scripts:$PATH"

# ─── 6. DinD 入口 ─────────────────────────────────────────────────────────
COPY runner/entrypoint.sh /usr/local/bin/sisyphus-entrypoint.sh
RUN chmod +x /usr/local/bin/sisyphus-entrypoint.sh

WORKDIR /workspace
ENV DOCKER_HOST="unix:///var/run/docker.sock" \
    SISYPHUS_RUNNER=1

ENTRYPOINT ["/usr/local/bin/sisyphus-entrypoint.sh"]
CMD ["sleep", "infinity"]
