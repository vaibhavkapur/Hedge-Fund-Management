-- PostgreSQL-compatible canonical schema for Integrated Hedge Fund Management Platform
-- Matches the table structures used by the local SQLite prototype store.

CREATE TABLE IF NOT EXISTS funds (
    fund_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS counterparties (
    counterparty_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    counterparty_id TEXT NOT NULL REFERENCES counterparties(counterparty_id),
    status TEXT NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS strategies (
    strategy_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    status TEXT NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    business_date DATE NOT NULL,
    is_late BOOLEAN NOT NULL DEFAULT FALSE,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fills_fund_date ON fills (fund_id, business_date);

CREATE TABLE IF NOT EXISTS manual_marks (
    business_date DATE NOT NULL,
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    payload_json JSONB NOT NULL,
    PRIMARY KEY (business_date, instrument_id)
);

CREATE TABLE IF NOT EXISTS journal_entries (
    entry_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    business_date DATE NOT NULL,
    source_ref TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_fund_date ON journal_entries (fund_id, business_date);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    business_date DATE NOT NULL,
    version INTEGER NOT NULL,
    is_frozen BOOLEAN NOT NULL DEFAULT FALSE,
    content_hash TEXT NOT NULL,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_fund_date ON portfolio_snapshots (fund_id, business_date);

CREATE TABLE IF NOT EXISTS reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    business_date DATE NOT NULL,
    is_clean BOOLEAN NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS close_runs (
    close_run_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    business_date DATE NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS exceptions (
    exception_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    business_date DATE NOT NULL,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    business_date DATE NOT NULL,
    report_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES portfolio_snapshots(snapshot_id),
    payload_json JSONB NOT NULL
);

-- Append-only tamper-evident hash-chained audit trail (revoke UPDATE/DELETE in production DB role)
CREATE TABLE IF NOT EXISTS audit_log (
    sequence_number BIGINT PRIMARY KEY,
    audit_id TEXT UNIQUE NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    payload_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS domain_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    occurred_at_utc TIMESTAMPTZ NOT NULL,
    fund_id TEXT,
    payload_json JSONB NOT NULL
);
