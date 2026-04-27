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
    # 把宿主 /dev/kvm 挂入 runner pod，给 Android emulator 走硬件加速。默认 false
    # 兼容没暴露 /dev/kvm 的部署（嵌套虚拟化 / 普通 dev 环境）。开启前操作员需在
    # 节点上跑 `kvm-ok` 确认 /dev/kvm 存在，否则 Pod 创建会卡 MountVolume.SetUp failed。
    runner_kvm_enabled: bool = False

    # in-cluster = orchestrator 跑在 K8s pod 里，load_incluster_config()
    # False = 本地调试，load_kube_config() 读 ~/.kube/config
    k8s_in_cluster: bool = True

    # PVC 保留策略：escalated REQ 保留时长（过期 GC 自动清，给 PR #48 resume 时间窗）
    # 默认 1 天：足够人当天 follow-up 续；超 1 天没人理就清掉
    # （之前 7 天太宽，一次实证时 30 个 PVC 堆满磁盘；2026-04-24）
    pvc_retain_on_escalate_days: int = 1
    # GC 扫描周期 15 min（之前 1h 太松，REQ 频繁时 PVC 堆得快）
    runner_gc_interval_sec: int = 900    # 15min
    # 磁盘压力阈值：超过此比例 GC 强清所有非 active PVC（不论 retention）
    runner_gc_disk_pressure_threshold: float = 0.8

    # ─── Admission gate（fresh REQ entry rate-limit）────────────────────────
    # 同时跑的 REQ 上限：start_intake / start_analyze 进门时数 req_state 里非
    # 终态行（exclude init/done/escalated/gh-incident-open + 自身），>= cap 直接
    # escalate。0 = 关闭。默认 10：vm-node04 6 GiB RAM、runner request 512Mi，
    # 10 个并发是调度器开始挤兑前的舒适上限。
    inflight_req_cap: int = 10
    # admission 阶段独立的磁盘阈值，比 runner_gc_disk_pressure_threshold(0.8) 更严：
    # 让"不收新活"先于"紧急清 PVC"触发，避免 GC tick 间隔（15 min）里继续建 PVC。
    admission_disk_pressure_threshold: float = 0.75

    # GitHub token（烘进 runner Pod env，给 agent 用 gh CLI / docker login ghcr.io）
    # scope: repo + read:packages
    # 注意：当 gh_incident_repo 非空时，本 PAT 必须额外有 Issues: Read-and-write
    # 才能在 ESCALATED 时开 GH 事故 issue（详见 gh_incident.py / orchestrator/helm/values.yaml）。
    github_token: str = ""
    github_pull_user: str = "x-access-token"

    # ─── REQ-impl-gh-incident-open-1777173133：ESCALATED 自动开 GH issue ─────
    # 任一 REQ 进 ESCALATED（real escalate，不是 auto-resume），sisyphus 用
    # github_token POST `/repos/{owner}/{repo}/issues` 开一条事故 issue，让人能在
    # `gh issue list --label sisyphus:incident` 看到。空 = 关闭（默认；dev / 未配
    # 部署不会写 GH）。生产建议设 `phona/sisyphus`。
    # 失败不阻塞 escalate：GH 5xx / 401 等只 log warning，REQ 仍进 ESCALATED。
    gh_incident_repo: str = ""
    gh_incident_labels: list[str] = Field(
        default_factory=lambda: ["sisyphus:incident"]
    )

    # Postgres
    pg_dsn: str = Field(..., description="postgresql://user:pass@host:5432/sisyphus")

    # 可观测性 DB（schema 见 observability/schema.sql，独立 db sisyphus_obs）
    # 留空 = 关闭观测写入和快照同步（dev 模式可不开）
    obs_pg_dsn: str = ""

    # bkd_snapshot 同步间隔（秒）。0 = 不跑（替代 n8n 5min cron）
    snapshot_interval_sec: int = 300

    # 已知死项目排除清单（snapshot loop 跳过 BKD list_issues 调用，避免 5min 一次
    # 的 snapshot.list_failed warning）。env 用逗号分隔或 JSON 数组：
    #   SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS=77k9z58j,old-proj
    snapshot_exclude_project_ids: list[str] = Field(default_factory=list)

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

    # ─── runner ready 重试 ─────────────────────────────────────────────
    # ensure_runner 等 Pod Ready 的外层 attempts：N × runner_ready_timeout_sec
    # 总等待；最后一次抛 TimeoutError 让 engine 走 escalate。
    # 默认 3 × 120s = 6min，覆盖 K3s 节点偶发慢启动；生产可调更高。
    runner_ready_attempts: int = 3

    # ─── agent model 控制 ─────────────────────────────────────────────────
    # sisyphus 起的 agent 用哪个模型（verifier / fixer / accept / pr_ci_watch /
    # done_archive / staging_test）。None = 用 BKD per-engine 默认（opus）。
    # 默认 claude-sonnet-4-6（成本低于 opus；测试 helm values 可覆盖成 'claude-haiku-4-5'）。
    # 注意：analyze agent 的 model 是 user 创 intent issue 时定的，sisyphus 不控；
    # analyze fan out 的 sub-issue model 由 analyze prompt 自己控（见 analyze.md.j2）。
    agent_model: str | None = "claude-opus-4-7[1m]"  # 实证：sonnet 在 code scope (single-file Dockerfile / new module) 跑 60-100min 反复 escalate，retry-token 总成本反而高于 opus 一次过；docs scope 仍可手动指定 sonnet (per-REQ model param)

    # ─── aissh-tao MCP server id (vm-node04) ─────────────────────────────
    # BKD agent 跑在 Coder workspace 没装 kubectl，所有 vm-node04 上的 kubectl 命令
    # 必须经 aissh-tao MCP 跨 SSH 跑。prompt 模板里需要这个 server_id 告诉 agent。
    # vm-node04 默认 id 见 runner_container.md.j2 的 fallback。helm values 可覆盖。
    aissh_server_id: str = "5b25f0cd-4fef-4a1f-a4c0-14ecf1395d84"

    # ─── REQ-clone-fallback-direct-analyze-1777119520：multi-layer involved_repos
    # 的 last-resort fallback 层（L4）。直接 analyze 路径（无 intake）+ ctx 没
    # involved_repos + tags 也没 `repo:<org>/<name>` → 用这里的 default 喂 server-side
    # clone helper。单仓部署（如 sisyphus 自 dogfood）配 `phona/sisyphus` 一条即可，
    # 直接 analyze 入口就能 auto-clone；多仓 / 跨项目部署留空（Field default_factory），
    # 强制用户走 intake 或在 intent issue 上挂 `repo:` tag 显式声明。
    # env：`SISYPHUS_DEFAULT_INVOLVED_REPOS=phona/sisyphus,phona/foo`（逗号分隔）
    # 或 JSON 数组 `["phona/sisyphus","phona/foo"]`。
    default_involved_repos: list[str] = Field(default_factory=list)

    # ─── M14b/M14c：verifier-agent 框架 ─────────────────────────────────
    # 每个 stage transition（成功 or 失败）先起一个 verifier-agent 做主观判断
    # —— 3 路决策：pass / fix / escalate，再由 webhook 路由推进状态机。
    # M14c 砍掉 M4 fail_kind 分类 + M5 bugfix/diagnose 子链，verifier 单独接管 fail 路径。
    # 砍 retry_checker：基础设施 flaky 由 verifier 判 escalate 给人，sisyphus 不机制性兜 retry。
    verifier_enabled: bool = True

    # ─── M8：watchdog 兜底卡死 stage ────────────────────────────────────
    # 周期扫 req_state，发现某 stage 超过阈值没 transition 且关联 BKD session
    # 不在 running → emit SESSION_FAILED 走 escalate。兜底 BKD spawn-time
    # 失败不发 webhook 的场景（M4 retry policy 假设"失败事件总会到"被打破）。
    watchdog_enabled: bool = True             # 默认开（兜底必须开）
    watchdog_interval_sec: int = 60           # 每 60s 扫一次
    watchdog_stuck_threshold_sec: int = 3600  # 60 min — sonnet analyze long tail 经常 25-35min；30 min 阈值会 false-escalate 大量 dogfood REQ；60 min 仍能兜真死

    # ─── 防 verifier↔fixer 死循环：硬封顶 fixer round 数 ─────────────────
    # 每次 verifier decision=fix 都会 start_fixer 起新一轮 fixer agent，跑完回 verifier
    # 复查；verifier 再判 fix 又起一轮。某些场景（spec 自相矛盾 / fixer 改不动根因）
    # 会无限循环。第 N+1 次 start_fixer 到达 cap → escalate（reason=fixer-round-cap）
    # 让人介入。N 默认 5；调高 = 给 fixer 更多机会，调低 = 更早叫人。
    fixer_round_cap: int = 5

    # ─── REQ-checker-infra-flake-retry-1777247423：infra-flake bounded retry ──
    # 三个 kubectl-exec checker（spec_lint / dev_cross_check / staging_test）一次跑挂时，
    # 若 stderr/stdout 命中 _flake.INFRA_FLAKE_PATTERNS（DNS / kubectl-channel /
    # github-fetch / registry-rate-limit 等），同 cmd 重跑 max 次（含原跑共 max+1
    # attempts），中间隔 backoff_sec。enabled=False = 关闭整套，行为退回 single-shot。
    # 真业务 fail（generic make Error / exit 137 / unauthorized）**不**触发 retry，
    # 留给 verifier 主观判 pass / fix / escalate。pr_ci_watch 不走这套（自有 HTTP
    # retry-until-deadline 模型）。
    checker_infra_flake_retry_enabled: bool = True
    checker_infra_flake_retry_max: int = 1            # 0 = no retry, 1 = 1 retry (2 attempts)
    checker_infra_flake_retry_backoff_sec: int = 15


settings = Settings()  # type: ignore[call-arg]
