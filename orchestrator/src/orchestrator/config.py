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

    # 入站 webhook 共享 token（BKD webhook 配置里加 X-Sisyphus-Token header）
    webhook_token: str = Field(..., description="X-Sisyphus-Token expected on /bkd-* webhooks")

    # Postgres
    pg_dsn: str = Field(..., description="postgresql://user:pass@host:5432/sisyphus")

    # 可观测性 DB（schema 见 observability/schema.sql，独立 db sisyphus_obs）
    # 留空 = 关闭观测写入和快照同步（dev 模式可不开）
    obs_pg_dsn: str = ""

    # bkd_snapshot 同步间隔（秒）。0 = 不跑（替代 n8n 5min cron）
    snapshot_interval_sec: int = 300

    # Project → repo URL（替代 router/router.js 里硬编码的 DEFAULT_PROJECT_REPO_MAP）
    # JSON: {"77k9z58j": "https://github.com/phona/ubox-crosser.git"}
    project_repo_map_json: str = '{"77k9z58j": "https://github.com/phona/ubox-crosser.git"}'

    # Workdir 模板（vm-node04 上的路径）
    workdir_root: str = "/var/sisyphus-ci"

    # Logging
    log_level: str = "INFO"
    log_json: bool = True


settings = Settings()  # type: ignore[call-arg]
