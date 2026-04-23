-- verifier_decisions：记录 verifier 的每次判决（M14e）。
-- 用于观察 verifier 决策质量：做了什么判决 → 实际后果是啥 → 判得对不对。
-- actual_outcome / decision_correct 由后续流程回填（pass/fail 明朗后由 store helper 标）。

CREATE TABLE IF NOT EXISTS verifier_decisions (
    id                  BIGSERIAL PRIMARY KEY,
    req_id              TEXT NOT NULL,
    stage               TEXT NOT NULL,
    trigger             TEXT NOT NULL,    -- 触发 verifier 的事件（check_fail / dev_done / ...）
    decision_action     TEXT,             -- retry / escalate / fix / pass / ...
    decision_fixer      TEXT,             -- 当 action=fix 时派谁修
    decision_scope      TEXT,             -- 修复范围（file / module / stage-wide）
    decision_reason     TEXT,
    decision_confidence TEXT,             -- high / medium / low
    made_at             TIMESTAMPTZ NOT NULL,
    actual_outcome      TEXT,             -- pass / fail / cancelled（后续回填）
    decision_correct    BOOLEAN           -- 判决是否被后续事实验证（后续回填）
);

CREATE INDEX IF NOT EXISTS idx_verifier_decisions_req ON verifier_decisions(req_id);
