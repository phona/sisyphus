"""Settings via env vars (12-factor)."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SISYPHUS_", env_file=".env", extra="ignore")

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8000

    # BKD
    bkd_base_url: str = "https://bkd-launcher--admin-jbcnet--weifashi.coder.tbc.5ok.co/api"
    bkd_token: str = Field(..., description="Coder-Session-Token")
    # transport: 'rest'（BKD ≥0.0.65 默认）/ 'mcp'（老版本，带 /api/mcp 端点）
    bkd_transport: str = "rest"

    # 入站 webhook 共享 token（BKD webhook 配置里加 `Authorization: Bearer <token>` header）
    webhook_token: str = Field(..., description="Bearer token expected in Authorization header on /bkd-events")

    # ─── v0.2 K8s runner 配置 ─────────────────────────────────────────────
    # sisyphus 在 sisyphus-runners namespace 拉起 per-REQ Pod（runner-<REQ>），
    # PVC（workspace-<REQ>）挂 /workspace。生命周期绑 REQ：done/escalate 时清理，
    # orchestrator 重启不销毁。
    runner_namespace: str = "sisyphus-runners"
    runner_image: str = "ghcr.io/phona/sisyphus-runner-go:main"
    runner_service_account: str = "sisyphus-runner-sa"
    runner_storage_class: str = "local-path"   # K3s 默认
    runner_workspace_size: str = "10Gi"        # PVC 大小（runner 峰值 ~5GB）
    # 单一 runner secret：keys = gh_token / ghcr_user / ghcr_token / kubeconfig
    # 前三个以 env 形式注入 Pod；kubeconfig 文件挂载到 /root/.kube/config
    runner_secret_name: str = "sisyphus-runner-secrets"
    runner_image_pull_secrets: list[str] = Field(default_factory=list)
    runner_ready_timeout_sec: int = 120

    # in-cluster = orchestrator 跑在 K8s pod 里，load_incluster_config()
    # False = 本地调试，load_kube_config() 读 ~/.kube/config
    k8s_in_cluster: bool = True

    # PVC 保留策略：escalated REQ 保留天数（过期 GC 自动清）
    pvc_retain_on_escalate_days: int = 7
    # GC 扫描周期
    runner_gc_interval_sec: int = 3600   # 1h

    # GitHub token（烘进 runner Pod env，给 agent 用 gh CLI / docker login ghcr.io）
    # scope: repo + read:packages
    github_token: str = ""
    github_pull_user: str = "x-access-token"

    # Postgres
    pg_dsn: str = Field(..., description="postgresql://user:pass@host:5432/sisyphus")

    # 可观测性 DB（schema 见 observability/schema.sql，独立 db sisyphus_obs）
    # 留空 = 关闭观测写入和快照同步（dev 模式可不开）
    obs_pg_dsn: str = ""

    # bkd_snapshot 同步间隔（秒）。0 = 不跑（替代 n8n 5min cron）
    snapshot_interval_sec: int = 300

    # 不存任何 repo / project_id：
    # - repo URL 由 agent 自己 `git remote get-url origin` 取（在 BKD session cwd 里）
    # - project_id 来自 webhook payload；snapshot 扫描 req_state.project_id distinct（多项目自然支持）

    # Workdir 模板（vm-node04 上的路径）
    workdir_root: str = "/var/sisyphus-ci"

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    # ─── Mock / 调试开关 ──────────────────────────────────────────────────
    # 临时跳过某 stage：对应 create_* action 不调 BKD agent，直接 emit *.done/.pass
    # 用于：ttpos-arch-lab 没接 → skip_accept；调试状态机 / done_archive → skip 全部
    # 生产环境全设 false
    skip_analyze: bool = False        # analyze.done
    skip_spec: bool = False           # spec.all-passed (跳整 spec stage)
    skip_dev: bool = False            # dev.done
    # v0.2：新 stage 替换 ci-unit/ci-int
    skip_staging_test: bool = False   # staging-test.pass（调试环境跑 unit+int）
    skip_pr_ci: bool = False          # pr-ci.pass（PR CI 全套等绿）
    skip_accept: bool = False         # accept.pass (ttpos-arch-lab 接好前默认 true)
    skip_reviewer: bool = False       # reviewer.pass (bugfix 子链)
    skip_archive: bool = False        # archive.done (跳过真 PR 创建)

    # 全部 skip = 状态机几秒走完，验 transition + cleanup，不动 BKD agent
    test_mode: bool = False           # 等价于全部 skip_* = true

    # ─── artifact-driven checker 开关（M1 灰度） ──────────────────────────
    # True = staging-test 由 sisyphus 自己 kubectl exec 跑，不再起 BKD agent
    # False（默认）= 走老路，创建 BKD agent issue（回滚：unset env / set false → rollout restart）
    checker_staging_test_enabled: bool = False

    # ─── M2：pr-ci-watch 自检 ────────────────────────────────────────────
    # True = sisyphus 用 GitHub REST API 轮询 PR check-runs，不再起 BKD agent
    # False（默认）= 走老路 BKD agent。回滚同上。
    checker_pr_ci_watch_enabled: bool = False
    # 轮询参数（仅 checker 模式下用）
    pr_ci_watch_poll_interval_sec: int = 30
    pr_ci_watch_timeout_sec: int = 1800   # 30 min


settings = Settings()  # type: ignore[call-arg]
