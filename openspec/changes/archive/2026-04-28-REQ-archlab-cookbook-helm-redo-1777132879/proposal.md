# REQ-archlab-cookbook-helm-redo-1777132879: docs(cookbook): rewrite ttpos-arch-lab accept-env cookbook to helm chart path (replace docker-compose)

## 问题

`docs/cookbook/ttpos-arch-lab-accept-env.md`（由 REQ-archlab-accept-env-cookbook-1777125697
引入）描述的是 docker-compose 路径：backend stack 和 Android emulator 都跑在 DinD
内，endpoint 用 `localhost:<随机 host port>`，ADB 用 `127.0.0.1:<随机 host port>`。

这套方案有三个问题：

1. **随机 host port 碰撞风险**：并发 REQ 各自跑 docker compose，host port 由 docker
   自动分配，理论上同时跑多个 REQ 可能撞端口，且端口数字不可预测。
2. **DinD 资源争抢**：多个 compose 项目共享同一个 DinD daemon，大量容器同时起时
   内存 / 磁盘 I/O 争抢影响稳定性。
3. **与 K3s 能力错配**：runner pod 已经注入 `KUBECONFIG` + helm 可用，是为 K3s 准备
   的，但 cookbook 却绕过了 K3s 直接用 DinD，浪费了 namespace 级别的天然隔离能力。

## 方案

**重写 cookbook 改用 helm 路径**：backend stack 和 Android emulator 各出一份 helm chart，
部到 K3s `$(SISYPHUS_NAMESPACE)` namespace（runner pod 有 KUBECONFIG，直接可用）。

核心改变：

| 维度 | 旧（docker-compose） | 新（helm） |
|---|---|---|
| backend 起法 | `docker compose up -d --wait` | `helm upgrade --install --wait --timeout 5m` |
| emulator 起法 | `docker compose up -d --wait` | `helm upgrade --install --wait --timeout 3m` + `kubectl exec` boot-wait |
| endpoint 地址 | `http://localhost:<随机 port>` | `http://lab.$NS.svc.cluster.local:8080`（固定 cluster DNS） |
| ADB 地址 | `127.0.0.1:<随机 port>` | `<emulator-adb ClusterIP>:5555`（固定 ClusterIP） |
| 隔离机制 | compose project name（同 host 共用 DinD） | K8s namespace（集群级隔离） |
| teardown | `docker compose down -v` | `helm uninstall` + `kubectl delete namespace` |

repo 布局从 docker-compose files（`docker-compose.accept.yml` / `emulator/docker-compose.emulator.yml`）
改为 helm charts（`charts/accept-lab/` / `charts/emulator/`）。

boot-wait 从 docker host 侧的 `emulator/boot-wait.sh`（`adb connect 127.0.0.1:$HOST_PORT`）
改为 `emulator/boot-wait-k8s.sh`（`kubectl exec deploy/emulator -- adb -s emulator-5554 shell getprop sys.boot_completed`）。

## 取舍

- **为什么 emulator 不加 readinessProbe**：软件渲染 boot 耗时 ~3 min，K8s readinessProbe
  反复失败会触发 restart；把 boot 检测移到 `boot-wait-k8s.sh`（runner pod 侧轮询
  kubectl exec）更可控，也不依赖 emulator 镜像内是否支持 shell health 命令。
- **为什么 ADB 用 ClusterIP 不用 NodePort**：runner pod 在集群内，ClusterIP 可达；
  NodePort 会占 host port（和 docker host port 同样有碰撞风险），不必要。
- **为什么不删 §4.2.2 docker-compose 模板**：有些业务团队 lab 不在 K3s 上（本地开发 /
  其他 CI），docker-compose 路径仍有效；两种路径共存，按环境选择。
- **KVM fallback 策略不变**：`-no-accel -gpu swiftshader_indirect` 软件渲染在
  sisyphus-runners namespace 的默认策略不变，只是从 docker 容器换到 K3s pod。
