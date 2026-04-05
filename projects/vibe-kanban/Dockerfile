# Vibe Kanban - 预下载完整包
FROM docker.m.daocloud.io/library/node:20-alpine

WORKDIR /app

# 安装 vibe-kanban 并在构建时预下载二进制文件
# 需要启动服务触发下载，然后复制缓存目录
RUN npm install -g vibe-kanban \
    && mkdir -p /root/.vibe-kanban \
    && timeout 60 sh -c 'npx vibe-kanban & sleep 15 && kill %1' || true

# 创建数据目录
RUN mkdir -p /data

# 暴露端口
EXPOSE 3000 3001

# 启动 vibe-kanban
CMD ["npx", "vibe-kanban"]
