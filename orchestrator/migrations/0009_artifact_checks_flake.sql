-- REQ-checker-infra-flake-retry-1777247423: artifact_checks 加 attempts + flake_reason
--
-- 三个 kubectl-exec checker（spec_lint / dev_cross_check / staging_test）现在会在
-- stderr/stdout 命中 infra-flake pattern 时同 cmd 重跑（详见
-- orchestrator/src/orchestrator/checkers/_flake.py + design.md）。
--
-- 新两列把 retry 元信息持久化，方便 Metabase 后续按 reason 聚合 infra-flake 比例：
--   - attempts:     总 exec 次数（含首次），无 retry = 1，retry 触发 ≥ 2
--   - flake_reason: NULL（未发生 retry / 真业务 fail）
--                   "flake-retry-recovered:<tag>"（重跑后 pass）
--                   "flake-retry-exhausted:<tag>"（重试用完仍 fail）
-- <tag> 取首次命中 pattern 的 tag（_flake.INFRA_FLAKE_PATTERNS）：dns / kubectl-exec-channel /
-- github-rpc / github-fetch / registry-rate-limit / registry-network / registry-5xx /
-- go-mod / npm-network / apt-mirror。
--
-- 兼容性：default 1 / NULL，旧 row 不动；migration idempotent (IF NOT EXISTS)。

ALTER TABLE artifact_checks
    ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 1;

ALTER TABLE artifact_checks
    ADD COLUMN IF NOT EXISTS flake_reason TEXT;

-- 偏置 partial index：看板查询通常按 flake_reason IS NOT NULL 过滤聚合，全表索引浪费
CREATE INDEX IF NOT EXISTS idx_artifact_checks_flake_reason
    ON artifact_checks(flake_reason)
    WHERE flake_reason IS NOT NULL;
