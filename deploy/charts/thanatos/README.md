# thanatos helm chart

Sisyphus 验收 harness 的 helm chart。M0 留在仓内（不发 OCI），业务仓
`accept-env-up` 在 M1 阶段会 `helm install` 这个 chart。

## driver 选择

`values.yaml` 顶层 `driver:` 决定 pod 拓扑：

| driver | Pod 形态 | container |
|---|---|---|
| `playwright` | 单容器 | `thanatos`（chromium subprocess inside python） |
| `http` | 单容器 | `thanatos` |
| `adb` | 双容器 | `redroid` (privileged, adb tcp:5555) + `thanatos`（sidecar，连 localhost:5555） |

`driver` 取其他值 `helm template` 直接 `fail`：

```
$ helm template . --set driver=desktop
Error: execution error at (...): thanatos.driver must be one of playwright|adb|http, got "desktop"
```

## 关键 values

| 字段 | 默认 | 说明 |
|---|---|---|
| `driver` | `playwright` | 见上 |
| `image.repository` | `ghcr.io/phona/thanatos` | M0 镜像不发 OCI；业务仓 override 自家镜像 |
| `image.tag` | `dev` | M0 |
| `redroid.image` | `redroid/redroid:11.0.0-latest` | 社区版默认；自托管换 `--set redroid.image=...` |
| `redroid.port` | `5555` | adb tcp 端口 |
| `service.type` / `service.port` | `ClusterIP` / `7000` | 只 debug 用，accept-agent 走 `kubectl exec` 不依赖 |

## 本地渲染检查

```bash
cd deploy/charts/thanatos
helm template . --set driver=playwright > /tmp/thanatos-pw.yaml
helm template . --set driver=adb        > /tmp/thanatos-adb.yaml
helm template . --set driver=http       > /tmp/thanatos-http.yaml
```

三条都 0 退码就 OK。`adb` 模式 deployment 应该有两个 container，其他模式只
有一个。

## 跟 lab chart 的关系

不做 sub-chart 关系。业务仓 `accept-env-up` 串联两次 `helm install`：先 lab
chart（integration repo 的），再 thanatos chart（这个）。owner / review 边
界跟着 chart 仓走。
