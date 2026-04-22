-- artifact_checks：记录 sisyphus 自检结果（M1 staging-test，后续扩 pr-ci-watch 等）。
-- 用于 watchdog / audit，不影响主状态机（主状态机靠 emit event 驱动）。

CREATE TABLE IF NOT EXISTS artifact_checks (
    id              BIGSERIAL PRIMARY KEY,
    req_id          TEXT NOT NULL,
    stage           TEXT NOT NULL,
    passed          BOOLEAN NOT NULL,
    exit_code       INT,
    cmd             TEXT,
    stdout_tail     TEXT,
    stderr_tail     TEXT,
    duration_sec    REAL,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_artifact_checks_req ON artifact_checks(req_id);
CREATE INDEX IF NOT EXISTS idx_artifact_checks_stage ON artifact_checks(stage, checked_at DESC);
