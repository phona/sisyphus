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
    # BKD frontend URL (for cross-link rendering). 留空 → links.bkd_issue_url
    # 自动从 bkd_base_url 剥 trailing /api 推导。生产 BKD 前后端同源时不必显式设；
    # 仅当前端跑在跟 /api 不同 host 上时覆盖（例如 BKD reverse-proxy 拆分部署）。
    bkd_frontend_url: str = ""

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
    # accept env GC 扫描周期；0 = 不跑（dev / 没接 accept-env-up 的部署可关）
    accept_env_gc_interval_sec: int = 900    # 15min

    # ─── Admission gate（fresh REQ entry rate-limit）────────────────────────
    # 同时跑的 REQ 上限：start_intake / start_execute 进门时数 req_state 里非
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

    # ─── DB migration 控制 ─────────────────────────────────────────────────
    # True = startup 跳过 yoyo migration（helm init-container 已跑完）
    skip_migration_on_startup: bool = False
    # yoyo 分布式锁等待超时（秒）。默认 300s，防止 K8s 多副本同时启动时
    # 排队 Pod 因 yoyo 默认 10s 超时 crashloop。
    migration_lock_timeout: int = 300

    # 可观测性 DB（schema 见 observability/schema.sql，独立 db sisyphus_obs）
    # 留空 = 关闭观测写入和快照同步（dev 模式可不开）
    obs_pg_dsn: str = ""

    # P0-2：config_version startup hook（默认 True — 例外说明：写入纯 metadata、幂等、
    # best-effort，对 prod 零侵入；flag 仅供 dev/test 消除无 git 环境的 debug log 噪音）
    config_version_startup_hook_enabled: bool = True

    # bkd_snapshot 同步间隔（秒）。0 = 不跑（替代 n8n 5min cron）
    snapshot_interval_sec: int = 300

    # 已知死项目排除清单（snapshot loop 跳过 BKD list_issues 调用，避免 5min 一次
    # 的 snapshot.list_failed warning）。env **必须是 JSON 数组**——pydantic-settings v2
    # 解析 list[str] 用 JSON decoder，csv `77k9z58j,old-proj` 会 SettingsError 启动崩
    # （issue #343 的实证 bug）。例：
    #   SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS='["77k9z58j","old-proj"]'
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
    # 用于：ttpos-arch-lab 没接 → skip_accept；调试状态机 → skip 全部
    # 生产环境全设 false
    skip_execute: bool = False        # execute.done
    skip_spec: bool = False           # spec.all-passed (跳整 spec stage)
    skip_dev: bool = False            # dev.done
    # v0.2：新 stage 替换 ci-unit/ci-int
    skip_staging_test: bool = False   # staging-test.pass（调试环境跑 unit+int）
    skip_pr_ci: bool = False          # pr-ci.pass（PR CI 全套等绿）
    skip_accept: bool = False         # accept.pass (ttpos-arch-lab 接好前默认 true)
    accept_smoke_delay_sec: int = 30  # env-up 后等服务起齐的 sleep（秒）
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

    # ─── ttpos-ci-direct-dispatch（REQ-447）──────────────────────────────
    # True = checker 路径进入轮询前主动 POST repository_dispatch 给 ci_dispatch_repo
    # False（默认）= 不主动 dispatch，依赖 PR webhook 被动触发 GHA
    ci_dispatch_enabled: bool = False
    # dispatch 目标仓，如 "phona/ttpos-ci"；空 = 关闭（即使 ci_dispatch_enabled=True）
    ci_dispatch_repo: str = ""
    # repository_dispatch event_type；ttpos-ci workflow 监听这个事件名
    ci_dispatch_event_type: str = "pr-ci-run"

    # ─── runner ready 重试 ─────────────────────────────────────────────
    # ensure_runner 等 Pod Ready 的外层 attempts：N × runner_ready_timeout_sec
    # 总等待；最后一次抛 TimeoutError 让 engine 走 escalate。
    # 默认 3 × 120s = 6min，覆盖 K3s 节点偶发慢启动；生产可调更高。
    runner_ready_attempts: int = 3

    # ─── agent model 控制 ─────────────────────────────────────────────────
    # sisyphus 起的 agent 用哪个模型（verifier / fixer / accept / pr_ci_watch /
    # staging_test）。None = 用 BKD per-engine 默认（opus）。
    # 默认 claude-sonnet-4-6（成本低于 opus；测试 helm values 可覆盖成 'claude-haiku-4-5'）。
    # 注意：analyze agent 的 model 是 user 创 intent issue 时定的，sisyphus 不控；
    # analyze fan out 的 sub-issue model 由 analyze prompt 自己控（见 execute.md.j2）。
    agent_model: str | None = "claude-opus-4-7[1m]"  # 实证：sonnet 在 code scope (single-file Dockerfile / new module) 跑 60-100min 反复 escalate，retry-token 总成本反而高于 opus 一次过；docs scope 仍可手动指定 sonnet (per-REQ model param)

    # ─── aissh-tao MCP server id (vm-node04) ─────────────────────────────
    # BKD agent 跑在 Coder workspace 没装 kubectl，所有 vm-node04 上的 kubectl 命令
    # 必须经 aissh-tao MCP 跨 SSH 跑。prompt 模板里需要这个 server_id 告诉 agent。
    # vm-node04 默认 id 见 runner_container.md.j2 的 fallback。helm values 可覆盖。
    aissh_server_id: str = "5b25f0cd-4fef-4a1f-a4c0-14ecf1395d84"

    # ─── REQ-feat-mcp-preflight-1777727213：MCP 依赖预检框架 ────────────────
    # 解决"agent 在缺 MCP 工具的 workspace 里硬撞工具名 7min 不报错卡死"问题。
    # 方案：用 capability 抽象 + provider 映射，让 prompt 模板从 var 渲染，
    # operator 改 helm values 即可换 provider，不必改 prompt 源码。
    #
    # stage_mcp_requirements：每个 stage 需要哪些 MCP capability。空列表 = 无依赖
    # （prompt 不渲染 preflight 段落）。verifier / fixer 阶段沿用对应 stage 的依赖。
    stage_mcp_requirements: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "intake": [],
            "execute": ["ssh_exec"],
            "challenger": ["ssh_exec"],
            "accept": ["ssh_exec"],
            "staging_test": [],
            "pr_ci_watch": [],
            "done_archive": [],
        },
        description="每个 stage 需要的 MCP capability 列表",
    )
    # mcp_capability_providers：capability → MCP provider 名称（即 mcp__<provider>__*
    # 的 provider 段）。换 provider 时改这里，prompt 里的 `mcp__{{ … }}__*` 同步生效。
    mcp_capability_providers: dict[str, str] = Field(
        default_factory=lambda: {
            "ssh_exec": "aissh-tao",
            "k8s_exec": "aissh-tao",
            "gh_cli": "gh",
        },
        description="capability → 默认 MCP provider 名称",
    )
    # mcp_capability_probe_tools：capability → MCP probe 工具名（即 preflight 段
    # 用来探活的 `mcp__<provider>__<tool>` 里的 <tool>）。换 MCP 后若工具表面跟着变
    # （aissh-tao 的 servers_list → 别家 list_servers），改这里就能让 prompt 同步。
    mcp_capability_probe_tools: dict[str, str] = Field(
        default_factory=lambda: {"ssh_exec": "servers_list"},
        description="capability → MCP probe 工具名（hook 渲染期注入到 mcp__<provider>__<tool>）",
    )

    # ─── REQ-feat-mcp-preflight-1777727213：可插拔 prompt hook ──────────────
    # _shared/hooks/<name>.md.j2 是「policy 类」prompt 片段（约束 / 外部依赖声明），
    # 跟具体 stage 业务无关、跨 stage 复用、operator 想改要做到不动模板源码。每个
    # stage prompt 在 hook slot 处 for-loop 渲所有 enabled 的 hook 文件；hook body
    # 自行靠 `stage` 变量决定要不要落地。
    # 默认三条：mcp_preflight（issue #270 的 fail-fast）+ precheck（issue #373
    # 的 env / 工具 / ci-precheck fail-fast）+ self_issue_constraint（只 PATCH
    # 自己 issue 的硬规矩）。顺序锁死：MCP 必须先就位，precheck 才能 exec_run 进 pod；
    # 两段 fail-fast 必须排在 self_issue_constraint 之前。operator 通过
    # SISYPHUS_ENABLED_PROMPT_HOOKS 全量覆盖（JSON 数组），不用 fork prompt。
    enabled_prompt_hooks: list[str] = Field(
        default_factory=lambda: ["mcp_preflight", "precheck", "self_issue_constraint"],
        description="按文件名约定加载的 prompt hook 列表（_shared/hooks/<name>.md.j2）",
    )

    # ─── REQ-feat-precheck-373-1777864856：stage agent step-0 precheck（#373）
    # 跟 stage_mcp_requirements 平行，决定 precheck hook 在哪些 stage 渲染段落。
    # 默认在所有「跑 runner pod」的 stage 都开（analyze / challenger / accept /
    # staging_test / pr_ci_watch / bugfix），intake / done_archive 关掉
    # （intake 是 chat brainstorm；done_archive 是 orchestrator 后台动作，不派 agent）。
    # 关注点分离 = 不复用 stage_mcp_requirements：
    #   - mcp_requirements 控「需要哪些 MCP capability」
    #   - precheck_enabled 控「是否做 env/tool/ci-precheck fail-fast」
    # operator 走 helm values（SISYPHUS_STAGE_PRECHECK_ENABLED=JSON dict）覆盖。
    stage_precheck_enabled: dict[str, bool] = Field(
        default_factory=lambda: {
            "intake": False,
            "execute": True,
            "challenger": True,
            "accept": True,
            "staging_test": True,
            "pr_ci_watch": True,
            "bugfix": True,
            "done_archive": False,
        },
        description="每个 stage 是否在 prompt 头部渲 precheck fail-fast 段",
    )

    # ─── REQ-clone-fallback-direct-analyze-1777119520：multi-layer involved_repos
    # 的 last-resort fallback 层（L4）。直接 analyze 路径（无 intake）+ ctx 没
    # involved_repos + tags 也没 `repo:<org>/<name>` → 用这里的 default 喂 server-side
    # clone helper。单仓部署（如 sisyphus 自 dogfood）配 `phona/sisyphus` 一条即可，
    # 直接 analyze 入口就能 auto-clone；多仓 / 跨项目部署留空（Field default_factory），
    # 强制用户走 intake 或在 intent issue 上挂 `repo:` tag 显式声明。
    # env：`SISYPHUS_DEFAULT_INVOLVED_REPOS=phona/sisyphus,phona/foo`（逗号分隔）
    # 或 JSON 数组 `["phona/sisyphus","phona/foo"]`。
    default_involved_repos: list[str] = Field(default_factory=list)

    # ─── REQ-base-branch-override-1777480690：全局默认 base branch ───────────
    # 当 BKD intent issue 没打 base:* tag、且 finalized intent 也没声明 base_branch
    # 时，用这个配置作为 fallback。解决"GitHub default branch = release 但开发主线是
    # develop"的场景：operator 配 `default_base_branch=develop`，所有 REQ 默认走 develop，
    # 特殊 REQ 再手动打 tag 覆盖。
    # 空字符串 = 不覆盖（走 origin/HEAD 兜底）。
    default_base_branch: str = ""
    # per-repo 默认 base branch：{"ttpos-flutter": "develop", "ttpos-server-go": "release"}
    # 优先级：tag > finalized_intent > per-repo settings > global settings > origin/HEAD
    default_base_branches: dict[str, str] = Field(default_factory=dict)

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
    # 2026-04-27 REQ-bkd-analyze-hang-debug-1777247423: ended-session fast lane。
    # SQL 预滤用 min(ended, stuck)，session_status != "running" 一旦 stuck >= ended 阈值
    # 立即 escalate；session_status == "running" 仍由 in-loop 跳过（不受影响）。
    # 把 "BKD agent 死了但没发 session.failed webhook" 的兜底从 60min 缩到 5min。
    watchdog_session_ended_threshold_sec: int = 300
    # REQ-fix-watchdog-liveness-1777809646: 活体探针。watchdog 在 escalate 决策前
    # 拉 BKD /logs 看最新 entry createdAt。距今 < 该值 → 视 agent 仍活，跳过 escalate。
    # 默认 120s 给单 turn 写大量代码留空间；BKD 仅返回 entry 后才会更新 createdAt，
    # 所以中间没 tool-result/assistant-message 的"思考期"也算静默——120s 是经验折中。
    watchdog_liveness_grace_sec: int = 120

    # ─── REQ-feat-stuck-notify-378-v2-1777866642：ESCALATED stale 主动通知 ──
    # 把"REQ 进 ESCALATED 没人 resume 永久卡"的检测延迟从 7 天（abandoned-by-user
    # sweep）压到分钟级。watchdog 每 5min 扫一次 state='escalated' AND
    # updated_at < NOW - escalated_stale_threshold_sec 的 row，每个 stale window
    # 通知一次（context.stuck_notified_at watermark 防重）。
    escalated_stale_notify_enabled: bool = True
    escalated_stale_threshold_sec: int = 1800        # 30min — issue #378 提议值
    # 可选 webhook，POST JSON `{"text": "..."}` 到该 URL。空 = 不外推（默认安全，
    # 仅靠 obs.record_event + log.warning 在内部 channel 暴露）。Telegram bot 用例：
    # `https://api.telegram.org/bot<TOKEN>/sendMessage`，payload 里附 chat_id
    # 由 nginx redirect / 反代 sidecar 注入；通用 webhook（飞书 / Slack 自定义
    # bot）也用同形 POST。失败不阻塞 tick，只 log warn。
    escalated_stale_telegram_url: str = ""

    # ─── 防 verifier↔fixer 死循环：硬封顶 fixer round 数 ─────────────────
    # 每次 verifier decision=fix 都会 start_fixer 起新一轮 fixer agent，跑完回 verifier
    # 复查；verifier 再判 fix 又起一轮。某些场景（spec 自相矛盾 / fixer 改不动根因）
    # 会无限循环。第 N+1 次 start_fixer 到达 cap → escalate（reason=fixer-round-cap）
    # 让人介入。N 默认 5；调高 = 给 fixer 更多机会，调低 = 更早叫人。
    fixer_round_cap: int = 5

    # ─── verifier 判 infra-flake 时的自动重跑次数上限 ────────────────────
    # verifier decision=retry（基础设施 flaky 判定）时，apply_verify_infra_retry
    # 从 ctx.infra_retry_count 读已重跑次数；< cap 则重跑 stage checker，
    # >= cap 则 emit VERIFY_ESCALATE（reason=infra-retry-cap）让人介入。
    # 默认 2：给 infra flake 2 次自愈机会，超了人工检查基础设施。
    verifier_infra_retry_cap: int = 2

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

    # ─── REQ-feat-agent-turns-collector-1777796671：BKD turn-level collector ──
    # 每 interval 秒扫 stage_runs 最近 24h 内关闭且有 bkd_issue_id 的行，
    # 拉 BKD /logs 折叠成 turn，upsert agent_turns 表。
    # False（默认）= 不跑（migration 跑完、collector 可用后再打开）。
    # best-effort：单 issue 失败只 log，不阻断整轮。
    agent_turns_collector_enabled: bool = False
    agent_turns_collector_interval_sec: int = 300         # 5min

    # ─── REQ-fix-pr-queue-health-monitoring-1777789759：PR drift cron ────────
    # 每 interval 秒扫 repos 列表的 OPEN PR，把落后 > threshold commit 的记入
    # pr_drift_log 供 Metabase Q19/Q20 看板。False = 不跑（默认；无 github_token 时
    # 自动跳过）。repos 格式：["phona/sisyphus"]（owner/name）。
    pr_health_enabled: bool = False
    pr_health_interval_sec: int = 1800          # 30min
    pr_health_repos: list[str] = Field(default_factory=list)
    pr_health_behind_threshold: int = 5         # <= 此数视为 fresh，跳过不记录

    # ─── 周期 TTL 清理（增长表防膨胀） ───────────────────────────────────
    # event_seen / dispatch_slugs / verifier_decisions / stage_runs(closed) 只增不减；
    # 后台任务按 interval 周期删过期行。False = 关闭（dev / 单测可不跑）。
    ttl_cleanup_enabled: bool = True
    ttl_cleanup_interval_sec: int = 86400         # 24h
    ttl_event_seen_days: int = 30                 # webhook dedup：7 天 4000+ 行，30 天足矣
    ttl_dispatch_slugs_days: int = 90             # slug 幂等映射：低频写，保 90 天
    ttl_verifier_decisions_days: int = 90         # verifier 判决审计：90 天
    ttl_stage_runs_closed_days: int = 90          # stage_runs ended_at IS NOT NULL：90 天


settings = Settings()  # type: ignore[call-arg]
