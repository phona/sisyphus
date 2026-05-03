# redroid-fixed

> 修过的 redroid Android-in-container 镜像，专门给 sisyphus thanatos
> chart 的 ADB driver 当 sidecar 用。

## 为啥要 fork

上游 `redroid/redroid:13.0.0_64only-latest` 在 **containerd 2.x**
（k3s 1.34+ 自带的 v2.2.x，runc 1.4.x）上**起不来**：

```
Error: failed to create containerd container:
  mount callback failed on /var/lib/rancher/k3s/agent/containerd/tmpmounts/...:
  openat etc/passwd=***FILTERED*** escapes from parent
```

containerd / runc 用 `openat2(RESOLVE_BENEATH)` 创建 OCI 标准的
`/etc/passwd` / `/etc/group` 等 sidecar 文件时做路径越界校验，redroid
rootfs 里 **`/etc/passwd` 不存在**，runc 的 path safefile 解析过程穿过
顶层绝对 symlink（`/bin → /system/bin` 之类）触发 "escapes from parent"。

vm-node04 实测确认：用 docker run / 旧 containerd 1.7 都能跑，但
**k3s 1.34 + containerd 2.2 这一组合必死**。

## 本镜像做了啥

1. `docker create` + `docker export | tar -x` 把 upstream rootfs 抽出来
   （**用 buildkit `COPY` 不行** —— buildkit 自己也踩同一个路径校验）
2. 在 `etc/passwd` / `etc/group` / `etc/shadow` 写最小 shell 账号信息
3. `tar -cf | docker import` 重打包，保留 `ENTRYPOINT ["/init"]` +
   `CMD ["qemu=1", "androidboot.hardware=redroid"]`

**不动 182 个绝对 symlink** —— vm-node04 实测显示 runc 只对它创建的文件
（如 /etc/passwd）做 RESOLVE_BENEATH 校验，redroid Android init 内部
syscall 不在校验范围。所以最小 patch 就够。

## tag 策略

| tag | 含义 |
|---|---|
| `13.0.0` | 跟随上游 `redroid/redroid:13.0.0_64only-latest`，本仓 patch 应用 |
| `latest` | 当前推荐 tag（= 13.0.0） |
| `sha-<7>` | 每次 commit 唯一 tag |

## 用法（thanatos chart）

```yaml
# values.yaml override
driver: adb
redroid:
  image: ghcr.io/phona/redroid-fixed:13.0.0
  pullPolicy: IfNotPresent
```

## host kernel 前置

redroid 需要 binder kernel module。Ubuntu 24.04 不默认带，需要：

```bash
sudo apt-get install -y linux-modules-extra-$(uname -r)
echo binder_linux | sudo tee /etc/modules-load.d/redroid.conf
echo 'options binder_linux num_devices=3' | sudo tee /etc/modprobe.d/redroid.conf
sudo modprobe binder_linux num_devices=3
```

## 已知不修

- 顶层 `/bin -> /system/bin` 等 182 个绝对 symlink —— 留着不影响
- 系统 bloat（SystemUI / Launcher / Phone 占 ~600 MB） —— accept stage
  跑一次 `pm disable-user` 即可（也可以做 image-level strip 但收益不大）
