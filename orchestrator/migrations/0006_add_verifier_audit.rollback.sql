-- rollback 0006：删 audit 列（nullable，drop 无数据丢失风险）。

ALTER TABLE verifier_decisions DROP COLUMN IF EXISTS audit;
