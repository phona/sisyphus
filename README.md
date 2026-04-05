# Sisyphus DevOps Platform

本地开发测试平台，一键部署 PostgreSQL + Gitea + n8n，支持完整的 CI/CD 工作流。

## 🚀 快速开始

### 前置要求

- [Docker Desktop](https://www.docker.com/products/docker-desktop)
- [kind](https://kind.sigs.k8s.io/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [helm](https://helm.sh/)
- [telepresence](https://www.telepresence.io/)（可选，用于服务访问）

### 1. 创建 Kind 集群

```bash
# 首次使用需创建集群
kind create cluster --name kind

# 或创建带更多资源的集群（推荐）
cat <<EOF | kind create cluster --name kind --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30000
        hostPort: 3000
        protocol: TCP
      - containerPort: 30001
        hostPort: 5678
        protocol: TCP
EOF
```

### 2. 一键启动平台

```bash
cd sisyphus

# 完整启动（检查镜像、部署服务、初始化仓库）
make start

# 或快速启动（如果已准备好镜像）
make quick-start
```

### 3. 访问服务

#### 方式一：Telepresence（推荐）

```bash
make telepresence-up

# 然后直接访问
open http://gitea-http.sisyphus.svc.cluster.local:3000
open http://n8n.sisyphus.svc.cluster.local:5678
```

#### 方式二：端口转发

```bash
# 终端 1
make port-forward-gitea

# 终端 2
make port-forward-n8n
```

## 📦 平台组件

| 组件 | 版本 | 访问地址 | 默认账号 |
|------|------|----------|----------|
| PostgreSQL | 18.5.15 | postgres-shared:5432 | postgres / devops |
| Gitea | 1.25.4 | http://gitea.local:3000 | gitea_admin / admin123 |
| n8n | 2.14.2 | http://n8n.local:5678 | admin / admin123 |

## 🗂️ 项目结构

```
sisyphus/
├── Makefile              # 统一命令入口
├── start.sh             # 一键启动脚本
├── deploy.sh            # 部署脚本
├── charts/              # Helm Charts（本地）
│   ├── postgresql/
│   ├── gitea/
│   ├── n8n/
│   └── gitea-act-runner.yaml
├── values/              # 配置文件
│   ├── postgres.yaml
│   ├── gitea.yaml
│   └── n8n.yaml
├── projects/            # 业务项目（Git Submodules）
│   ├── ttpos-flutter/   # Flutter 前端
│   └── ttpos-server-go/ # Go 后端
└── docs/                # 文档
```

## 🛠️ 常用命令

### 平台管理

```bash
make help          # 显示所有命令
make start         # 完整启动
make quick-start   # 快速启动
make stop          # 停止服务（保留数据）
make restart       # 重启服务
make status        # 查看状态
make clean         # 清理所有资源（⚠️ 数据丢失）
make logs          # 查看日志
```

### 单独部署

```bash
make deploy-postgres   # 仅部署 PostgreSQL
make deploy-gitea      # 仅部署 Gitea
make deploy-n8n        # 仅部署 n8n
make deploy-all        # 部署所有组件
```

### 代码管理

```bash
make init-repos        # 初始化并推送代码到 Gitea
make push-all          # 推送所有项目
make push-flutter      # 仅推送 Flutter
make push-go           # 仅推送 Go
```

### 测试

```bash
make test-all          # 运行所有项目 CI
make test-flutter      # 仅测试 Flutter
make test-go           # 仅测试 Go

# 或在项目目录内
cd projects/ttpos-flutter && make ci
cd projects/ttpos-server-go && make ci
```

## 🔧 CI/CD 工作流

### 项目 Makefile 接口

每个项目都包含标准的 `Makefile`，提供统一的 CI 接口：

**ttpos-flutter:**
```bash
make ci      # lint + test + build
make lint    # flutter analyze
make test    # flutter test
make build   # flutter build apk
make dev     # flutter run
```

**ttpos-server-go:**
```bash
make ci              # lint + test + build
make lint            # golangci-lint
make test            # go test
make test-coverage   # 生成覆盖率报告
make build           # go build
make docker-build    # docker build
make dev             # go run
```

### Gitea Actions

推送代码到 `v2.20.0-ai-rebuild` 或 `release` 分支会自动触发 CI：

```bash
git push gitea v2.20.0-ai-rebuild
```

工作流配置位于：
- `projects/ttpos-flutter/.gitea/workflows/ci.yml`
- `projects/ttpos-server-go/.gitea/workflows/ci.yml`

## 🌐 Git 仓库地址

| 项目 | Gitea 地址 | 本地路径 |
|------|-----------|----------|
| sisyphus | http://gitea.local/gitea_admin/sisyphus | ./ |
| ttpos-flutter | http://gitea.local/gitea_admin/ttpos-flutter | ./projects/ttpos-flutter/ |
| ttpos-server-go | http://gitea.local/gitea_admin/ttpos-server-go | ./projects/ttpos-server-go/ |

## 🐛 故障排除

### 镜像拉取失败

```bash
# 手动拉取并加载到 kind
docker pull <image>
kind load docker-image <image> --name kind
```

### 资源不足

```bash
# 检查资源使用
kubectl top nodes
kubectl describe node kind-control-plane

# 清理不必要的 Pod
kubectl delete pod --field-selector=status.phase!=Running -n sisyphus
```

### Gitea 无法访问

```bash
# 检查 Pod 状态
kubectl get pods -n sisyphus

# 查看日志
kubectl logs -n sisyphus deployment/gitea --tail=50

# 重启 Gitea
kubectl rollout restart deployment/gitea -n sisyphus
```

### 数据库连接失败

```bash
# 进入 PostgreSQL 检查
make shell-postgres

# 手动创建数据库
psql -U postgres -c "CREATE DATABASE gitea;"
psql -U postgres -c "CREATE DATABASE n8n;"
```

## 📝 配置说明

### 修改数据库密码

编辑 `values/postgres.yaml`：

```yaml
auth:
  postgresPassword: your-new-password
```

然后重新部署：

```bash
make deploy-postgres
```

### 修改 Gitea 管理员密码

```bash
kubectl create secret generic gitea-admin-secret \
  --namespace sisyphus \
  --from-literal=username=gitea_admin \
  --from-literal=password=your-new-password \
  --dry-run=client -o yaml | kubectl apply -f -
```

## 🤝 贡献

1. 在本地修改代码
2. 运行 `make test-all` 确保通过测试
3. 提交并推送到 Gitea
4. 通过 Gitea Actions CI 验证

## 📄 许可证

MIT License
