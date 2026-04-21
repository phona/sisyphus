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

    # 入站 webhook 共享 token（BKD webhook 配置里加 `Authorization: Bearer <token>` header）
    webhook_token: str = Field(..., description="Bearer token expected in Authorization header on /bkd-events")

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
    skip_ci_unit: bool = False        # ci-unit.pass
    skip_ci_int: bool = False         # ci-int.pass
    skip_accept: bool = False         # accept.pass (ttpos-arch-lab 接好前默认 true)
    skip_reviewer: bool = False       # reviewer.pass (bugfix 子链)
    skip_archive: bool = False        # archive.done (跳过真 PR 创建)

    # 全部 skip = 状态机几秒走完，验 transition + cleanup，不动 BKD agent
    test_mode: bool = False           # 等价于全部 skip_* = true


settings = Settings()  # type: ignore[call-arg]
