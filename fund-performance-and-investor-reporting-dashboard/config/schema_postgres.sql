-- PostgreSQL canonical schema for Fund Performance and Investor Reporting Dashboard
-- Compatible with SQLite adapter via standard ANSI SQL subsets where applicable.
-- All timestamps are stored as UTC ISO-8601 strings / TIMESTAMPTZ.

CREATE TABLE IF NOT EXISTS funds (
    fund_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_currency CHAR(3) NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('SIMULATED', 'PAPER', 'LIVE')),
    data_status TEXT NOT NULL CHECK (data_status IN ('SYNTHETIC', 'RECONCILED_SANDBOX', 'UNAUDITED', 'AUDITED')),
    inception_date DATE NOT NULL,
    benchmark_id TEXT NOT NULL,
    management_fee_bps DOUBLE PRECISION NOT NULL,
    performance_fee_pct DOUBLE PRECISION NOT NULL,
    hurdle_rate_annual DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    high_water_mark_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS nav_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    valuation_date DATE NOT NULL,
    as_of_utc TIMESTAMPTZ NOT NULL,
    availability_utc TIMESTAMPTZ NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    currency CHAR(3) NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('SIMULATED', 'PAPER', 'LIVE')),
    data_status TEXT NOT NULL,
    opening_nav DOUBLE PRECISION NOT NULL,
    gross_pnl DOUBLE PRECISION NOT NULL,
    management_fee DOUBLE PRECISION NOT NULL,
    performance_fee DOUBLE PRECISION NOT NULL,
    other_expenses DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    subscriptions DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    redemptions DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    closing_nav DOUBLE PRECISION NOT NULL,
    shares_outstanding DOUBLE PRECISION NOT NULL,
    cash_balance DOUBLE PRECISION NOT NULL,
    gross_exposure DOUBLE PRECISION NOT NULL,
    net_exposure DOUBLE PRECISION NOT NULL,
    ledger_reconciled BOOLEAN NOT NULL DEFAULT FALSE,
    ledger_hash TEXT NOT NULL,
    provenance_source TEXT NOT NULL,
    UNIQUE (fund_id, valuation_date, revision)
);

CREATE TABLE IF NOT EXISTS cash_flows (
    flow_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    snapshot_id TEXT NOT NULL REFERENCES nav_snapshots(snapshot_id),
    valuation_date DATE NOT NULL,
    as_of_utc TIMESTAMPTZ NOT NULL,
    flow_type TEXT NOT NULL CHECK (flow_type IN ('SUBSCRIPTION', 'REDEMPTION', 'FEE_SETTLEMENT', 'DISTRIBUTION')),
    amount DOUBLE PRECISION NOT NULL,
    currency CHAR(3) NOT NULL,
    timing TEXT NOT NULL CHECK (timing IN ('START_OF_DAY', 'END_OF_DAY')),
    investor_class TEXT NOT NULL DEFAULT 'CLASS_A',
    environment TEXT NOT NULL CHECK (environment IN ('SIMULATED', 'PAPER', 'LIVE'))
);

CREATE TABLE IF NOT EXISTS fx_rates (
    rate_id TEXT PRIMARY KEY,
    valuation_date DATE NOT NULL,
    as_of_utc TIMESTAMPTZ NOT NULL,
    base_currency CHAR(3) NOT NULL,
    quote_currency CHAR(3) NOT NULL,
    rate DOUBLE PRECISION NOT NULL CHECK (rate > 0),
    source TEXT NOT NULL,
    UNIQUE (valuation_date, base_currency, quote_currency)
);

CREATE TABLE IF NOT EXISTS benchmark_returns (
    benchmark_id TEXT NOT NULL,
    valuation_date DATE NOT NULL,
    currency CHAR(3) NOT NULL,
    daily_return DOUBLE PRECISION NOT NULL,
    level DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (benchmark_id, valuation_date)
);

CREATE TABLE IF NOT EXISTS strategy_snapshots (
    strategy_snapshot_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES nav_snapshots(snapshot_id),
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    valuation_date DATE NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    currency CHAR(3) NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('SIMULATED', 'PAPER', 'LIVE')),
    weight DOUBLE PRECISION NOT NULL,
    gross_pnl DOUBLE PRECISION NOT NULL,
    transaction_costs DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    long_exposure DOUBLE PRECISION NOT NULL,
    short_exposure DOUBLE PRECISION NOT NULL,
    cash_allocation DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS reports (
    version_id TEXT PRIMARY KEY,
    report_series_id TEXT NOT NULL,
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    revision_number INTEGER NOT NULL,
    supersedes_version_id TEXT REFERENCES reports(version_id),
    revision_reason TEXT,
    audience TEXT NOT NULL CHECK (audience IN ('INTERNAL', 'INVESTOR')),
    reporting_currency CHAR(3) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    as_of_utc TIMESTAMPTZ NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('SIMULATED', 'PAPER', 'LIVE')),
    data_status TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'SUBMITTED', 'REVIEWED', 'APPROVED', 'PUBLISHED', 'REJECTED', 'SUPERSEDED')),
    created_by TEXT NOT NULL,
    reviewed_by TEXT,
    approved_by TEXT,
    published_at_utc TIMESTAMPTZ,
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (report_series_id, revision_number)
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id TEXT PRIMARY KEY,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('ALLOW', 'DENY', 'ERROR')),
    details_json TEXT NOT NULL
);
