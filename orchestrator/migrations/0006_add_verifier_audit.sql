-- fixer diff audit 可观测性（fixer-audit REQ）。
-- verifier-after-fix 在 decision JSON 里附带 diff audit 报告，落库到 audit JSONB 列。
-- 老行 audit = NULL，向后兼容；Q14/Q15/Q16 看板已有 WHERE audit IS NOT NULL 过滤。

ALTER TABLE verifier_decisions ADD COLUMN IF NOT EXISTS audit JSONB;
