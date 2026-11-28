-- PostgreSQL 15+ schema for Investor Subscription and Redemption Platform (prototype)
-- Uses NUMERIC for exact decimal arithmetic and append-only tables for journal and audit logs.

CREATE TABLE IF NOT EXISTS investors (
    investor_id TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    investor_type TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    tax_form_status TEXT NOT NULL,
    accredited BOOLEAN NOT NULL,
    bank_account_ref TEXT NOT NULL,
    identity_doc_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    onboarding_checks_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS dealing_windows (
    window_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    share_class TEXT NOT NULL,
    dealing_date DATE NOT NULL,
    open_at TIMESTAMPTZ NOT NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    cash_due_date DATE NOT NULL,
    settlement_date DATE NOT NULL,
    UNIQUE(fund_id, share_class, dealing_date)
);

CREATE TABLE IF NOT EXISTS nav_records (
    nav_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    share_class TEXT NOT NULL,
    dealing_date DATE NOT NULL,
    currency CHAR(3) NOT NULL,
    nav_per_share NUMERIC(20, 6) NOT NULL CHECK (nav_per_share > 0),
    version INTEGER NOT NULL CHECK (version >= 1),
    status TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    supersedes_nav_id TEXT REFERENCES nav_records(nav_id),
    restatement_reason TEXT
);

CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE NOT NULL,
    investor_id TEXT NOT NULL REFERENCES investors(investor_id),
    fund_id TEXT NOT NULL,
    share_class TEXT NOT NULL,
    request_type TEXT NOT NULL,
    dealing_date DATE NOT NULL,
    currency CHAR(3) NOT NULL,
    gross_amount NUMERIC(20, 2),
    requested_shares NUMERIC(24, 6),
    submitted_at TIMESTAMPTZ NOT NULL,
    maker_id TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    checker_id TEXT,
    approved_at TIMESTAMPTZ,
    nav_id TEXT REFERENCES nav_records(nav_id),
    nav_version INTEGER,
    fee_amount NUMERIC(20, 2),
    net_cash NUMERIC(20, 2),
    calculated_shares NUMERIC(24, 6),
    rounding_residual NUMERIC(24, 10),
    cancellation_reason TEXT,
    cancelled_by TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    maker_id TEXT NOT NULL,
    checker_id TEXT NOT NULL CHECK (lower(maker_id) <> lower(checker_id)),
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    direction TEXT NOT NULL,
    currency CHAR(3) NOT NULL,
    amount NUMERIC(20, 2) NOT NULL CHECK (amount > 0),
    value_date DATE NOT NULL,
    bank_reference TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    match_status TEXT NOT NULL,
    discrepancy_amount NUMERIC(20, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS exceptions (
    exception_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    exception_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    details_json JSONB NOT NULL,
    reviewer_id TEXT,
    resolution_note TEXT,
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS share_journal (
    entry_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    investor_id TEXT NOT NULL REFERENCES investors(investor_id),
    fund_id TEXT NOT NULL,
    share_class TEXT NOT NULL,
    currency CHAR(3) NOT NULL,
    dealing_date DATE NOT NULL,
    entry_type TEXT NOT NULL,
    share_delta NUMERIC(24, 6) NOT NULL,
    gross_cash_delta NUMERIC(20, 2) NOT NULL,
    fee_delta NUMERIC(20, 2) NOT NULL,
    net_cash_delta NUMERIC(20, 2) NOT NULL,
    nav_id TEXT NOT NULL REFERENCES nav_records(nav_id),
    nav_version INTEGER NOT NULL,
    posted_at TIMESTAMPTZ NOT NULL,
    posted_by TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_entry_id TEXT REFERENCES share_journal(entry_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence BIGINT PRIMARY KEY,
    event_id TEXT UNIQUE NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    prev_hash CHAR(64) NOT NULL,
    entry_hash CHAR(64) NOT NULL
);
