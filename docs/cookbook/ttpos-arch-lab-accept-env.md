# Cookbook: ttpos-arch-lab `accept-env-up` / `accept-env-down`

> 给 **integration repo `phona/ttpos-arch-lab`**（以及任何形似的「mobile e2e lab」repo）
> 一份能直接抄的 `make accept-env-up` / `make accept-env-down` 食谱：起 backend
> helm chart → 起 Android emulator helm chart → 等 boot 完成 → 编 APK → 装 APK
> → emit endpoint JSON → 让 sisyphus accept-agent 跑 FEATURE-A* scenarios。
>
> 适用场景：被测系统是 **「mobile App + 后端 stack」**，accept-agent 要同时面向
> HTTP endpoint（验业务接口）和 emulator 上 App（验 UI 流程）。lab 跑在
> K3s（sisyphus runner pod 里有 `KUBECONFIG`，能直接 `helm` / `kubectl`）。
>
> 契约权威是 [`docs/integration-contracts.md`](../integration-contracts.md) §2.3 / §3 / §5；
> 本 cookbook 只是一份**实现样板**。冲突以契约文档为准。

## 0. TL;DR — 这份 recipe 给你什么

`make accept-env-up` 执行 5 步，**stdout 只在最后吐一行 JSON**（其余写 stderr）：

1. `helm upgrade --install lab charts/accept-lab --namespace $NS --create-namespace --wait --timeout 5m`
2. `helm upgrade --install emulator charts/emulator --namespace $NS --wait --timeout 3m` + `kubectl exec` 轮 `sys.boot_completed=1`
3. `flutter build apk --release --dart-define=API_BASE_URL=http://lab.$NS.svc.cluster.local:8080` 编 APK（或拉 prebuilt）
4. `adb connect <emulator-ClusterIP>:5555` + `adb install -r` 装到 emulator
5. `printf '{"endpoint":"http://lab.$NS.svc.cluster.local:8080","adb":"<ClusterIP>:5555",...}\n'`

`make accept-env-down` 反向：`helm uninstall emulator` → `helm uninstall lab` → `kubectl delete namespace $NS`，
全部 best-effort（`|| true`）。

## 1. repo 布局

`ttpos-arch-lab` 是 sisyphus 契约里的 **integration repo**，
runner pod 把它 clone 到 `/workspace/integration/ttpos-arch-lab/`。整个 lab 自包含：
backend helm chart + emulator helm chart + APK 来源声明 + Makefile，全在一个仓里。

```
ttpos-arch-lab/
├── Makefile                              ← accept-env-up / accept-env-down
├── charts/
│   ├── accept-lab/                       ← backend helm chart
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/
│   │       ├── deployment.yaml
│   │       ├── service.yaml
│   │       └── postgres-statefulset.yaml
│   └── emulator/                         ← Android emulator helm chart
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── deployment.yaml
│           └── service.yaml
├── emulator/
│   └── boot-wait-k8s.sh                  ← kubectl exec 等 boot_completed 的辅助脚本
├── apk/
│   ├── source.txt                        ← APK 来源声明（git URL 或 GHCR artifact tag）
│   └── build.sh                          ← 拉 source / 编 APK / 输出到 ./apk/dist/app.apk
└── README.md                             ← 指回这份 cookbook
```

不强制目录细节，但 **Makefile + helm charts + APK build script 三件套缺一不可**。

## 2. backend helm chart：`charts/accept-lab`

`Chart.yaml` 最小声明：

```yaml
apiVersion: v2
name: accept-lab
version: 0.1.0
description: ttpos backend stack for sisyphus accept lab
```

`values.yaml`（注入点，Makefile 按需 override）：

```yaml
serverTag: main           # ghcr.io/phona/ttpos-server-go:<tag>
postgresPassword: ttpos
```

`templates/deployment.yaml` 要点：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: lab
  namespace: {{ .Release.Namespace }}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: lab
  template:
    metadata:
      labels:
        app.kubernetes.io/name: lab
    spec:
      containers:
      - name: lab
        image: ghcr.io/phona/ttpos-server-go:{{ .Values.serverTag }}
        env:
        - name: DATABASE_URL
          value: postgres://ttpos:{{ .Values.postgresPassword }}@postgres:5432/ttpos?sslmode=disable
        ports:
        - containerPort: 8080
        readinessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
          failureThreshold: 12
          successThreshold: 1
```

`templates/service.yaml`（ClusterIP，无需 NodePort）：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: lab
  namespace: {{ .Release.Namespace }}
spec:
  selector:
    app.kubernetes.io/name: lab
  ports:
  - port: 8080
    targetPort: 8080
    name: http
```

endpoint 由此固定为 `http://lab.<namespace>.svc.cluster.local:8080` ——
runner pod 在同一 K3s 集群内，cluster DNS 直通，**不需要 host port 随机分配**。

同 release 里可放 postgres StatefulSet / redis / mock 第三方等所有 backend 依赖。

## 3. emulator helm chart：`charts/emulator`

`Chart.yaml`：

```yaml
apiVersion: v2
name: emulator
version: 0.1.0
description: headless Android emulator for ttpos accept lab
```

`values.yaml`：

```yaml
image: ghcr.io/cirruslabs/android-images:api-34
emulatorArgs: "-no-window -no-audio -no-snapshot -gpu swiftshader_indirect -no-accel"
```

`templates/deployment.yaml`：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: emulator
  namespace: {{ .Release.Namespace }}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: emulator
  template:
    metadata:
      labels:
        app.kubernetes.io/name: emulator
    spec:
      containers:
      - name: emulator
        image: {{ .Values.image }}
        securityContext:
          privileged: true           # ADB binder + /dev/kvm 探测 需要
        env:
        - name: EMULATOR_ARGS
          value: {{ .Values.emulatorArgs | quote }}
        ports:
        - containerPort: 5555        # adb daemon
        - containerPort: 5554        # emulator console（仅 debug 用）
        resources:
          requests:
            memory: "2Gi"
            cpu: "1"
          limits:
            memory: "4Gi"
            cpu: "2"
```

`templates/service.yaml`（暴露 ADB 给同集群 runner pod）：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: emulator-adb
  namespace: {{ .Release.Namespace }}
spec:
  selector:
    app.kubernetes.io/name: emulator
  ports:
  - port: 5555
    targetPort: 5555
    name: adb
```

**为什么不加 readinessProbe**：emulator 需要 ~3 min 软件模拟 boot 到
`sys.boot_completed=1`，K8s readinessProbe 不适合跑这么长的 init 检测
（会反复重启 pod）。`helm --wait` 只等 pod Running 状态，boot_completed
检测由 `emulator/boot-wait-k8s.sh` 在 runner pod 里单独完成（见 §3.1）。

**KVM 说明**：vm-node04 K3s sisyphus-runners 不暴露 `/dev/kvm`，
默认走 `-no-accel -gpu swiftshader_indirect` 软件渲染。boot 时间从
~30s 涨到 ~3 min，accept stage timeout 1800s 内完全可接受。
如果 host 确实有 KVM 模块（`lsmod | grep kvm`），去掉 `-no-accel` 可提速。

### 3.1 `emulator/boot-wait-k8s.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

NS="${1:?usage: boot-wait-k8s.sh <namespace>}"

# 1. 等 pod Running（helm --wait 已做，此处二次确认 + 给 readinessProbe-less pod 兜底）
echo "[boot-wait] waiting for emulator pod Ready..." >&2
kubectl -n "$NS" wait \
    --for=condition=ready pod \
    -l app.kubernetes.io/name=emulator \
    --timeout=600s >&2

# 2. 轮 sys.boot_completed=1（通过 kubectl exec 进 emulator 容器）
echo "[boot-wait] waiting for sys.boot_completed=1..." >&2
for i in $(seq 1 120); do
    result=$(kubectl -n "$NS" exec deploy/emulator -- \
        sh -c "adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null || echo 0" \
        2>/dev/null | tr -d '\r\n')
    if [ "$result" = "1" ]; then
        echo "[boot-wait] emulator boot_completed (attempt $i)" >&2
        exit 0
    fi
    echo "[boot-wait] attempt $i/120, not ready yet, retry in 5s..." >&2
    sleep 5
done

echo "[boot-wait] timeout: emulator never reached boot_completed" >&2
exit 1
```

`kubectl exec deploy/emulator` 进 emulator 容器内跑 `adb -s emulator-5554`：
emulator 进程跑在容器内，ADB daemon 也在容器内，`emulator-5554` 是 ADB 侧的
虚拟串口名，直接可用。

## 4. APK 构建：`apk/build.sh`

与 docker-compose cookbook 几乎相同，仅 `API_BASE_URL` 从 localhost 随机端口
改为 cluster DNS（helm 部署后 endpoint 固定，不需要运行时解析）：

```bash
#!/usr/bin/env bash
# apk/build.sh — 编 APK 写到 ./apk/dist/app.apk
set -euo pipefail

DIST="$(cd "$(dirname "$0")" && pwd)/dist"
mkdir -p "$DIST"

SRC_REPO="${TTPOS_FLUTTER_REPO:-/workspace/source/ttpos-flutter-app}"

if [[ ! -d "$SRC_REPO" ]]; then
    echo "[apk/build] $SRC_REPO not found; falling back to GHCR prebuilt" >&2
    curl -fsSL \
        -H "Authorization: Bearer $SISYPHUS_GHCR_TOKEN" \
        "https://ghcr.io/v2/phona/ttpos-flutter-app/blobs/${TTPOS_APK_DIGEST:?missing}" \
        -o "$DIST/app.apk"
    exit 0
fi

cd "$SRC_REPO"
flutter pub get
flutter build apk --release \
    --dart-define=API_BASE_URL="${TTPOS_API_BASE_URL:?must point to cluster lab endpoint}"

cp build/app/outputs/flutter-apk/app-release.apk "$DIST/app.apk"
echo "[apk/build] APK -> $DIST/app.apk" >&2
```

`TTPOS_API_BASE_URL` 由 Makefile 在调 `build.sh` 前设好（见 §6）：
```
http://lab.$(SISYPHUS_NAMESPACE).svc.cluster.local:8080
```

cluster DNS 格式由 K8s 解析，runner pod 跑在同一集群内所以直通。

## 5. endpoint JSON 契约（多键扩展）

`integration-contracts.md` §3 规定 stdout 末行 JSON **必须**有 `endpoint` 字段。
mobile lab 在此基础上**再加两个非必需键**给 accept-agent 用：

```json
{
  "endpoint": "http://lab.accept-req-archlab-xxx.svc.cluster.local:8080",
  "adb": "10.43.85.121:5555",
  "apk_package": "com.phona.ttpos",
  "namespace": "accept-req-archlab-xxx"
}
```

| key | 必需 | 给谁用 |
|---|---|---|
| `endpoint` | ✅ | accept-agent 跑后端 HTTP scenarios |
| `adb` | （扩展） | accept-agent 跑 UI scenarios，先 `adb connect <addr>` 再 `adb shell ...` |
| `apk_package` | （扩展） | accept-agent 启 App 用 `am start -n <package>/<activity>` |
| `namespace` | （可选） | 跟 §3 一致，重写一遍方便 debug |

> `endpoint` 和 `adb` 都是**集群内地址**（cluster DNS / ClusterIP），accept-agent
> 跑在 runner pod 里，runner pod 在同一 K3s 集群内，直接可达。
>
> **额外键由 accept-agent 自己读**——sisyphus orchestrator 只解析 `endpoint`。
> accept-agent prompt 模板 (`prompts/accept.md.j2`) 里能直接用
> `ctx.lab.adb` / `ctx.lab.apk_package`。

## 6. 完整 Makefile 范本

```makefile
.PHONY: accept-env-up accept-env-down

# sisyphus 注入；防御性兜底
SISYPHUS_NAMESPACE ?= accept-default
APK_PACKAGE ?= com.phona.ttpos

accept-env-up:
	@# 1. 起 backend helm chart
	@echo "[arch-lab] up: backend ($(SISYPHUS_NAMESPACE))" >&2
	helm upgrade --install lab charts/accept-lab \
	    --namespace $(SISYPHUS_NAMESPACE) --create-namespace \
	    --wait --timeout 5m >&2
	@# 2. 起 emulator helm chart（等 pod Running；boot_completed 由 boot-wait-k8s.sh 检测）
	@echo "[arch-lab] up: emulator" >&2
	helm upgrade --install emulator charts/emulator \
	    --namespace $(SISYPHUS_NAMESPACE) \
	    --wait --timeout 3m >&2
	@# 3. 等 emulator boot_completed（kubectl exec 进容器轮询）
	./emulator/boot-wait-k8s.sh "$(SISYPHUS_NAMESPACE)" >&2
	@# 4. 取 emulator ADB ClusterIP（runner pod 在集群内，可直接访问 ClusterIP）
	@adb_host=$$(kubectl -n $(SISYPHUS_NAMESPACE) get svc emulator-adb \
	    -o jsonpath='{.spec.clusterIP}'); \
	if [ -z "$$adb_host" ]; then \
	    echo "[arch-lab] FAIL: cannot resolve emulator-adb ClusterIP" >&2; \
	    exit 3; \
	fi; \
	echo "$$adb_host" > .accept-adb-host
	@# 5. 编 APK（API_BASE_URL 用 cluster DNS，不需要 host port 解析）
	@TTPOS_API_BASE_URL="http://lab.$(SISYPHUS_NAMESPACE).svc.cluster.local:8080" \
	    ./apk/build.sh >&2
	@# 6. 连 ADB + 装 APK
	@adb_host=$$(cat .accept-adb-host); \
	adb connect "$$adb_host:5555" >&2; \
	adb -s "$$adb_host:5555" install -r ./apk/dist/app.apk >&2
	@# 7. emit endpoint JSON（**stdout 末行**，前面所有日志写 stderr）
	@adb_host=$$(cat .accept-adb-host); \
	printf '{"endpoint":"http://lab.%s.svc.cluster.local:8080","adb":"%s:5555","apk_package":"%s","namespace":"%s"}\n' \
	    "$(SISYPHUS_NAMESPACE)" "$$adb_host" "$(APK_PACKAGE)" "$(SISYPHUS_NAMESPACE)"

accept-env-down:
	-@# best-effort：每一步独立失败不阻塞下一步
	-helm uninstall emulator --namespace $(SISYPHUS_NAMESPACE) 2>/dev/null || true
	-helm uninstall lab --namespace $(SISYPHUS_NAMESPACE) 2>/dev/null || true
	-kubectl delete namespace $(SISYPHUS_NAMESPACE) --ignore-not-found 2>/dev/null || true
	-rm -f .accept-adb-host 2>/dev/null || true
```

要点：

- **stdout / stderr 严格分离**：`integration-contracts.md` §3 规定
  `result.stdout.splitlines()` 反向取第一个非空行。recipe 里所有日志走 stderr（`>&2`），
  stdout 只有最后一行 `printf`。
- **helm namespace 隔离**：每个 REQ 有独立 `$SISYPHUS_NAMESPACE`（如
  `accept-req-archlab-xxx`），`helm upgrade --install --create-namespace` 幂等，
  并发 REQ 互不干扰，不再依赖 docker host port 随机分配。
- **ADB ClusterIP 而非 localhost port**：runner pod 在 K3s 集群内，
  `kubectl get svc emulator-adb -o jsonpath='{.spec.clusterIP}'` 给出稳定 IP，
  ADB 连接走 cluster 网络，比 docker host port 更可预测。
- **`.accept-adb-host` 落盘**：helm steps 之间需共享 ClusterIP，写文件比 `$(shell ...)` 稳；
  env-down 删掉。
- **`accept-env-down` 全 best-effort**：partial up 失败时（emulator helm 没起来）
  也必须把 backend release + namespace 清掉，每步 `|| true` 保证不因前步失败而短路。

## 6.5 加 thanatos 验收 harness（thanatos M1 起的可选拓扑）

> 启用条件：业务仓有 `.thanatos/skill.yaml`（详见
> [`docs/integration-contracts.md`](../integration-contracts.md) §10）。
> **不**启用 → 这一节整段跳过，§6 模板照旧能跑（accept-agent 走 curl fallback）。

启用后业务仓 `accept-env-up` 在装完 lab + emulator 之后**多装一个 thanatos pod**，
跟 lab 共用 `$SISYPHUS_NAMESPACE`，再把 pod 名吐进 endpoint JSON 的 `thanatos_pod`
字段，accept-agent 看到这个字段就走 thanatos branch（`kubectl exec <pod> --
python -m thanatos run-scenario ...`）。

### 6.5.1 chart 来源

thanatos chart 在 sisyphus 仓内 `deploy/charts/thanatos/`。runner pod 已 clone
sisyphus → 路径 `/workspace/source/sisyphus/deploy/charts/thanatos`。约定 env
变量 `SISYPHUS_THANATOS_CHART` 给业务仓 Makefile override，默认就是这个路径。

### 6.5.2 完整 Makefile 片段（在 §6 模板基础上 patch 进来）

```makefile
# §6 顶上原有 vars 之外新增
SISYPHUS_THANATOS_CHART ?= /workspace/source/sisyphus/deploy/charts/thanatos
THANATOS_DRIVER ?= adb         # mobile App 用 adb；纯 web 改 playwright，纯 API 改 http

accept-env-up:
	@# §6 既有 1~6 步保持不变（backend / emulator / boot-wait / APK build / install） ...
	@# 6.5 NEW：装 thanatos harness（同 namespace，共享 lab pod 网络访问）
	@echo "[arch-lab] up: thanatos ($(THANATOS_DRIVER))" >&2
	helm upgrade --install thanatos $(SISYPHUS_THANATOS_CHART) \
	    --namespace $(SISYPHUS_NAMESPACE) \
	    --set driver=$(THANATOS_DRIVER) \
	    --wait --timeout 2m >&2
	@kubectl -n $(SISYPHUS_NAMESPACE) wait \
	    --for=condition=ready pod -l app.kubernetes.io/name=thanatos \
	    --timeout=2m >&2
	@thanatos_pod=$$(kubectl -n $(SISYPHUS_NAMESPACE) get pod \
	    -l app.kubernetes.io/name=thanatos \
	    -o jsonpath='{.items[0].metadata.name}'); \
	if [ -z "$$thanatos_pod" ]; then \
	    echo "[arch-lab] FAIL: cannot resolve thanatos pod name" >&2; \
	    exit 4; \
	fi; \
	echo "$$thanatos_pod" > .accept-thanatos-pod
	@# 7. emit endpoint JSON（**stdout 末行**）—— 在 §6 原 printf 基础上多吐 thanatos_pod
	@adb_host=$$(cat .accept-adb-host); \
	thanatos_pod=$$(cat .accept-thanatos-pod); \
	printf '{"endpoint":"http://lab.%s.svc.cluster.local:8080","adb":"%s:5555","apk_package":"%s","namespace":"%s","thanatos_pod":"%s"}\n' \
	    "$(SISYPHUS_NAMESPACE)" "$$adb_host" "$(APK_PACKAGE)" "$(SISYPHUS_NAMESPACE)" "$$thanatos_pod"

accept-env-down:
	-@# §6 既有 cleanup 之前先 uninstall thanatos
	-helm uninstall thanatos --namespace $(SISYPHUS_NAMESPACE) 2>/dev/null || true
	-rm -f .accept-thanatos-pod 2>/dev/null || true
	-@# 然后 §6 既有的 emulator / lab uninstall + ns delete + adb host file rm 照旧
	# ...
```

要点：

- **driver=adb 时多容器拓扑**：thanatos chart `_helpers.tpl::assertDriver` 校验
  driver 必须 ∈ {playwright, adb, http}。adb 模式下 thanatos pod 含 redroid
  sidecar；这跟你 §3 的 emulator chart **是两回事**（你那个 emulator 是给 mobile
  App 跑的，thanatos 的 redroid 是给 thanatos 自己探查产品 UI 用的）。
  仓库强约束：M1 mobile lab 推荐 thanatos 走 `playwright` 或 `http` 驱动 backend
  HTTP，emulator 仍由你 §3 的 chart 起 —— thanatos M1 driver 全 `NotImplementedError`，
  现在换哪个 driver 都返同一个 stub-only failure_hint。
- **wait + label 取 pod name**：thanatos chart helm release name 不固定（这里
  我们硬编 `thanatos`），通过 label `app.kubernetes.io/name=thanatos` 找；同 chart
  M0 测试 (`test_contract_thanatos.py::THAN-S6/S7`) 锁了这个 label 形状。
- **endpoint JSON 字段拼接**：跟 §5 endpoint JSON 多键扩展协议一致 —— accept-agent
  prompt 透传 `accept_env` 整 dict，多吐字段不会破坏现有行为。

### 6.5.3 不启用 thanatos 时怎么办

直接 **不**改 §6 模板：

- 不 `helm install thanatos`
- endpoint JSON 不吐 `thanatos_pod`
- accept-agent 看到 `thanatos_pod` 缺失 → 自动 curl fallback

所以 thanatos opt-in 是真正可选 —— 业务仓不需要先准备 thanatos 才能上 sisyphus
accept stage。

## 7. 跟 sisyphus accept-agent 的对接

accept-agent prompt (`orchestrator/src/orchestrator/prompts/accept.md.j2`) 已经会
读 `ctx.lab.endpoint`。多键扩展（`adb` / `apk_package`）由 accept-agent 自己用。
推荐 prompt 段写：

```text
本 REQ 是 mobile App + 后端 stack 联合 e2e。lab 暴露：

- HTTP endpoint：{{ ctx.lab.endpoint }}（hit 后端业务 API）
- ADB：{{ ctx.lab.adb }}（emulator 已起 + APK {{ ctx.lab.apk_package }} 已装）

跑 UI scenario 时：
  adb connect {{ ctx.lab.adb }}
  adb -s {{ ctx.lab.adb }} shell am start -n {{ ctx.lab.apk_package }}/.MainActivity
  adb -s {{ ctx.lab.adb }} shell input tap <x> <y>
  adb -s {{ ctx.lab.adb }} shell screencap -p > /tmp/screen.png

跑 backend scenario 时：
  curl -fsS {{ ctx.lab.endpoint }}/api/...
```

如果业务 prompt 没改 / `ctx.lab` 只读 `endpoint`：accept-agent 就只跑 HTTP 部分，
mobile UI scenarios 跳过 —— **优雅降级**：`endpoint` 是契约，缺它 fail；
`adb` 是扩展，缺它降级。

## 8. 排查清单

`make accept-env-up` 失败时按这个顺序看：

| 症状 | 先看 |
|---|---|
| backend helm install 失败 | `kubectl -n $NS describe pod -l app.kubernetes.io/name=lab` + `kubectl -n $NS logs deploy/lab` |
| backend readiness 超时 | `kubectl -n $NS logs deploy/lab` 看 healthz 是否返回 200；检查 postgres StatefulSet 是否 Ready |
| emulator helm install 失败（pod OOM / CrashLoop） | `kubectl -n $NS describe pod -l app.kubernetes.io/name=emulator`；memory limit 涨到 4Gi |
| `boot-wait-k8s.sh` 超时 | `kubectl -n $NS exec deploy/emulator -- adb devices` 看 emulator-5554 是否 `device`；`EMULATOR_ARGS` 里加 `-verbose` 抓日志 |
| `kubectl get svc emulator-adb` ClusterIP 为空 | 检查 `charts/emulator/templates/service.yaml` 里 selector 标签跟 pod 模板一致 |
| `adb connect <ClusterIP>:5555` 失败 | runner pod 跑 `nc -zv <ClusterIP> 5555` 测 TCP 连通；ClusterIP 在集群内可达，不需要 NodePort |
| `adb install` 报 `INSTALL_FAILED_INVALID_APK` | APK abi 跟 emulator 不一致，build.sh 加 `--target-platform android-x64` |
| stdout 末行不是 JSON | `make accept-env-up 2>/dev/null \| tail -1` 检查哪步漏了 `>&2` |
| sisyphus 解析不到 `endpoint` | endpoint URL 必须是 runner pod 视角可达的 cluster DNS，不要用 pod IP（重建会变） |

## 9. 跟现有契约 / 历史模板的关系

- `docs/integration-contracts.md` §4.2 helm-based 模板：纯后端 stack（无 emulator）的最小骨架；
  本 cookbook 在它基础上加了 emulator chart + APK build + ADB 三件套。
- `docs/integration-contracts.md` §4.2.2 docker-compose 通用模板：给不在 K3s 上的团队；
  如果你的 runner 没有 `KUBECONFIG`（本地开发 / 其他 CI），仍可用 docker-compose 路径。
- `docs/cookbook/` 后续可能再加 `flutter-mock-backend.md` 等：每份 cookbook 限定一种 lab 形态，互不重叠。

> **从 docker-compose 迁移到 helm 的动因**：
> docker-compose 路径需要随机 host port 分配，增加了端口碰撞风险；并发 REQ 跑多个
> compose 项目时 DinD daemon 资源争抢。helm 路径利用 K8s namespace 天然隔离，
> endpoint 地址固定（cluster DNS），不依赖 host 端口分配，
> 且 runner pod 已有 `KUBECONFIG`，helm / kubectl 无额外依赖。
