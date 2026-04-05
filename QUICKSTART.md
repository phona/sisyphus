# Sisyphus 快速参考

## 🎯 最简启动（3 步）

```bash
# 1. 确保 kind 集群存在
kind create cluster --name kind 2>/dev/null || true

# 2. 启动平台
make start

# 3. 连接网络
make telepresence-up
```

## 🧪 开始测试

```bash
# 测试 Flutter
cd projects/ttpos-flutter
make ci

# 测试 Go
cd projects/ttpos-server-go
make ci
```

## 📋 常用命令速查

| 命令 | 说明 |
|------|------|
| `make start` | 完整启动 |
| `make stop` | 停止服务（保留数据） |
| `make restart` | 重启服务 |
| `make status` | 查看状态 |
| `make clean` | 清理所有（⚠️ 数据丢失） |
| `make test-all` | 测试所有项目 |
| `make logs` | 查看日志 |

## 🔗 服务地址

| 服务 | 地址 | 账号 |
|------|------|------|
| Gitea | http://gitea-http.sisyphus.svc.cluster.local:3000 | gitea_admin / admin123 |
| n8n | http://n8n.sisyphus.svc.cluster.local:5678 | admin / admin123 |
| PostgreSQL | postgres-shared:5432 | postgres / devops |

## 🆘 故障处理

```bash
# 服务起不来
make logs                    # 查看日志
kubectl get pods -n sisyphus # 查看 Pod 状态

# 镜像拉取失败
kind load docker-image <image> --name kind

# 重置所有
make clean
make start
```
