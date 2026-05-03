-- REQ-fix-req-termination-accounting-1777789757: REQ 终态结果记账
--
-- 回答"这周烧的 token 里多少是被 reject 的 REQ 浪费的？"
-- terminal_outcome 由多个 hook 协同填充（best-effort，标不到留 NULL）：
--   merged              → engine.step 推 DONE 时，或 escalate PR-merged shortcut 时
--   sisyphus-escalated  → escalate action 写入 ESCALATED 时
--   abandoned-by-user   → watchdog 扫到 ESCALATED + 7 天无续时
--   pr-rejected         → best-effort（当前版本留 NULL，后续 GH webhook 接管）
--   abandoned-by-user / merged-then-reverted / pr-closed-no-merge / replaced-by-other-req
--                       → 同上 best-effort，未覆盖部分留 NULL

ALTER TABLE req_state ADD COLUMN IF NOT EXISTS terminal_outcome TEXT;

COMMENT ON COLUMN req_state.terminal_outcome IS
  'merged / merged-then-reverted / pr-rejected / pr-closed-no-merge / abandoned-by-user / replaced-by-other-req / sisyphus-escalated';

CREATE INDEX IF NOT EXISTS idx_req_state_terminal_outcome
    ON req_state (terminal_outcome) WHERE terminal_outcome IS NOT NULL;
