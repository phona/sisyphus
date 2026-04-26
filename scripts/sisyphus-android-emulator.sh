#!/usr/bin/env bash
# sisyphus-android-emulator.sh — boot/halt the default Android AVD inside a runner pod.
#
# 只在 full Flutter runner image 里有用（go.Dockerfile 同样 COPY 了这个文件，但缺
# emulator/avdmanager 二进制 → 调用即 fail-fast 报 "emulator: command not found"，
# 让 caller 看到清楚错误而不是被埋）。
#
# 业务 Makefile 一行调用：
#   /opt/sisyphus/scripts/sisyphus-android-emulator.sh boot           # 默认 300s timeout
#   /opt/sisyphus/scripts/sisyphus-android-emulator.sh boot --timeout 600
#   /opt/sisyphus/scripts/sisyphus-android-emulator.sh halt
#
# 依赖宿主 /dev/kvm 已挂入 pod（orchestrator runner.kvmEnabled=true）。
# /dev/kvm 缺失 → emulator 自动退回软 CPU，仍能跑但慢 5-10x；脚本会 warning 不
# fail，让没装 KVM 的 dev 环境也可以验功能。
set -euo pipefail

AVD_NAME="${AVD_NAME:-sisyphus-default}"
TIMEOUT_SEC=300
PID_FILE="/tmp/sisyphus-android-emulator.pid"

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  boot [--timeout N]   Start AVD '${AVD_NAME}' headless and wait until boot_completed=1
  halt                 Kill the running emulator started by this script

Env overrides:
  AVD_NAME             AVD name to boot (default: sisyphus-default)
EOF
}

cmd_boot() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --timeout) TIMEOUT_SEC="$2"; shift 2 ;;
      *) echo "[android-emulator] unknown option: $1" >&2; usage; exit 2 ;;
    esac
  done

  if ! command -v emulator >/dev/null 2>&1; then
    echo "[android-emulator] emulator: command not found — this image is the Go-only flavor; switch to ghcr.io/phona/sisyphus-runner:main" >&2
    exit 127
  fi
  if ! command -v adb >/dev/null 2>&1; then
    echo "[android-emulator] adb: command not found (Android platform-tools missing from image)" >&2
    exit 127
  fi

  if [[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]]; then
    echo "[android-emulator] /dev/kvm available — will use KVM acceleration"
  else
    echo "[android-emulator] WARNING: /dev/kvm not accessible — falling back to software CPU (5-10x slower). Set runner.kvmEnabled=true in helm values to fix." >&2
  fi

  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    echo "[android-emulator] emulator already running (pid=$(cat "${PID_FILE}"))"
  else
    echo "[android-emulator] starting AVD '${AVD_NAME}'…"
    nohup emulator -avd "${AVD_NAME}" \
      -no-window -no-audio -no-snapshot \
      -gpu swiftshader_indirect \
      -no-boot-anim -accel on \
      >/var/log/android-emulator.log 2>&1 &
    echo $! > "${PID_FILE}"
  fi

  echo "[android-emulator] adb wait-for-device (timeout ${TIMEOUT_SEC}s)…"
  adb start-server >/dev/null 2>&1 || true

  local started end
  started="$(date +%s)"
  end=$(( started + TIMEOUT_SEC ))

  # adb wait-for-device 自身没有 timeout 选项，包一层
  while [[ "$(date +%s)" -lt "${end}" ]]; do
    if adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' | grep -q '^1$'; then
      local elapsed=$(( $(date +%s) - started ))
      echo "[android-emulator] boot completed in ${elapsed}s"
      return 0
    fi
    sleep 2
  done

  echo "[android-emulator] timed out after ${TIMEOUT_SEC}s waiting for sys.boot_completed=1" >&2
  echo "[android-emulator] last 30 lines of /var/log/android-emulator.log:" >&2
  tail -n 30 /var/log/android-emulator.log >&2 || true
  return 1
}

cmd_halt() {
  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid="$(cat "${PID_FILE}")"
    if kill -0 "${pid}" 2>/dev/null; then
      echo "[android-emulator] killing emulator pid=${pid}"
      kill "${pid}" || true
      # 等最多 10s 让 emulator 干净退（QEMU 收 SIGTERM 后还要 flush）
      for _ in $(seq 1 10); do
        kill -0 "${pid}" 2>/dev/null || break
        sleep 1
      done
      kill -9 "${pid}" 2>/dev/null || true
    fi
    rm -f "${PID_FILE}"
  fi
  adb kill-server 2>/dev/null || true
}

case "${1:-}" in
  boot) shift; cmd_boot "$@" ;;
  halt) shift; cmd_halt ;;
  -h|--help|help|"") usage; exit 0 ;;
  *) echo "unknown command: $1" >&2; usage; exit 2 ;;
esac
