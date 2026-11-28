-- Prime Broker Collateral Management — PostgreSQL Immutable Journal Schema
-- Requires PostgreSQL 14+. Corrections are appended as new versioned rows; UPDATE and DELETE are blocked.

CREATE TABLE IF NOT EXISTS statement_versions (
    entry_seq           BIGSERIAL PRIMARY KEY,
    statement_id        TEXT        NOT NULL UNIQUE,
    version             INTEGER     NOT NULL CHECK (version >= 1),
    supersedes_id       TEXT        NULL,
    broker_id           TEXT        NOT NULL,
    account_id          TEXT        NOT NULL,
    business_date       DATE        NOT NULL,
    base_currency       CHAR(3)     NOT NULL,
    content_hash        CHAR(64)    NOT NULL,
    payload_json        JSONB       NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (broker_id, account_id, business_date, version)
);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    run_seq             BIGSERIAL PRIMARY KEY,
    run_id              TEXT        NOT NULL UNIQUE,
    statement_id        TEXT        NOT NULL REFERENCES statement_versions(statement_id),
    broker_id           TEXT        NOT NULL,
    account_id          TEXT        NOT NULL,
    business_date       DATE        NOT NULL,
    policy_id           TEXT        NOT NULL,
    policy_version      INTEGER     NOT NULL,
    base_currency       CHAR(3)     NOT NULL,
    posted_collateral   NUMERIC(24, 8) NOT NULL,
    margin_requirement  NUMERIC(24, 8) NOT NULL,
    net_accrual_base    NUMERIC(24, 8) NOT NULL,
    break_count         INTEGER     NOT NULL,
    reconciled_clean    BOOLEAN     NOT NULL,
    result_json         JSONB       NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS margin_calls (
    call_seq            BIGSERIAL PRIMARY KEY,
    call_id             TEXT        NOT NULL UNIQUE,
    run_id              TEXT        NOT NULL REFERENCES reconciliation_runs(run_id),
    broker_id           TEXT        NOT NULL,
    account_id          TEXT        NOT NULL,
    business_date       DATE        NOT NULL,
    base_currency       CHAR(3)     NOT NULL,
    action              TEXT        NOT NULL,
    call_amount         NUMERIC(24, 8) NOT NULL,
    flagged_pre_settle  BOOLEAN     NOT NULL,
    approval_status     TEXT        NOT NULL,
    payload_json        JSONB       NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS substitution_reviews (
    review_seq          BIGSERIAL PRIMARY KEY,
    review_id           TEXT        NOT NULL UNIQUE,
    request_id          TEXT        NOT NULL,
    broker_id           TEXT        NOT NULL,
    account_id          TEXT        NOT NULL,
    business_date       DATE        NOT NULL,
    settlement_permitted BOOLEAN    NOT NULL,
    approval_status     TEXT        NOT NULL,
    required_top_up     NUMERIC(24, 8) NOT NULL,
    payload_json        JSONB       NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION reject_journal_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Journal table % is append-only; record corrections as new versions', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_no_mutate_statement_versions ON statement_versions;
CREATE TRIGGER trg_no_mutate_statement_versions
    BEFORE UPDATE OR DELETE ON statement_versions
    FOR EACH ROW EXECUTE FUNCTION reject_journal_mutation();

DROP TRIGGER IF EXISTS trg_no_mutate_reconciliation_runs ON reconciliation_runs;
CREATE TRIGGER trg_no_mutate_reconciliation_runs
    BEFORE UPDATE OR DELETE ON reconciliation_runs
    FOR EACH ROW EXECUTE FUNCTION reject_journal_mutation();

DROP TRIGGER IF EXISTS trg_no_mutate_margin_calls ON margin_calls;
CREATE TRIGGER trg_no_mutate_margin_calls
    BEFORE UPDATE OR DELETE ON margin_calls
    FOR EACH ROW EXECUTE FUNCTION reject_journal_mutation();

DROP TRIGGER IF EXISTS trg_no_mutate_substitution_reviews ON substitution_reviews;
CREATE TRIGGER trg_no_mutate_substitution_reviews
    BEFORE UPDATE OR DELETE ON substitution_reviews
    FOR EACH ROW EXECUTE FUNCTION reject_journal_mutation();
