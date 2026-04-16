# 阶段1: 安装 Claude CLI
FROM docker.m.daocloud.io/library/alpine:latest AS claude-installer

# 创建 root 的 .local/bin 目录
RUN mkdir -p /root/.local/bin

# 安装 curl 和 bash
RUN apk add --no-cache curl bash

# 设置 PATH 让安装脚本知道安装位置
ENV PATH="/root/.local/bin:${PATH}"

# 安装 Claude CLI
RUN curl -fsSL https://claude.ai/install.sh | bash

# Vibe Kanban - 预下载完整包
FROM docker.m.daocloud.io/library/node:20-alpine

WORKDIR /app

# 从 installer 阶段复制 Claude 二进制文件
COPY --from=claude-installer /root/.local/bin/claude /usr/local/bin/claude

# 安装 git (Claude CLI 需要) 和 bash (Claude Bash tool 需要)
RUN apk add --no-cache git bash

# 安装 vibe-kanban 并在构建时预下载二进制文件
# 需要启动服务触发下载，然后复制缓存目录
RUN npm install -g vibe-kanban \
    && mkdir -p /root/.vibe-kanban \
    && timeout 300 sh -c 'npx vibe-kanban & sleep 60 && kill %1' || true

# 创建数据目录
RUN mkdir -p /data

# 暴露端口
EXPOSE 3000 3001

# 启动 vibe-kanban
CMD ["npx", "vibe-kanban"]
