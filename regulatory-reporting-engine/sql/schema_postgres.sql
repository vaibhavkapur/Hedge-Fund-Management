-- PostgreSQL DDL for Regulatory Reporting Engine (Immutable Snapshots, Report Versions, Approval Audit)
-- Mirror of the SQLite offline store used for local deterministic testing.
-- All monetary quantities are persisted as exact NUMERIC(24, 6) or canonical string-encoded JSON payloads.

CREATE TABLE IF NOT EXISTS governed_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    fund_id             TEXT NOT NULL,
    as_of_date          DATE NOT NULL,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    base_currency       CHAR(3) NOT NULL,
    payload_sha256      CHAR(64) NOT NULL,
    payload_json        JSONB NOT NULL,
    ingested_at_utc     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (fund_id, period_start, period_end, payload_sha256)
);

CREATE TABLE IF NOT EXISTS regulatory_reports (
    report_id               TEXT PRIMARY KEY,
    filing_key              TEXT NOT NULL,
    version_number          INTEGER NOT NULL CHECK (version_number >= 1),
    supersedes_report_id    TEXT REFERENCES regulatory_reports(report_id),
    correction_reason       TEXT,
    snapshot_id             TEXT NOT NULL REFERENCES governed_snapshots(snapshot_id),
    profile_id              TEXT NOT NULL,
    schema_version          TEXT NOT NULL,
    mapping_id              TEXT NOT NULL,
    mapping_version         TEXT NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN ('DRAFT', 'REVIEWED', 'APPROVED', 'SUPERSEDED', 'REJECTED')),
    content_sha256          CHAR(64) NOT NULL,
    fields_json             JSONB NOT NULL,
    lineage_json            JSONB NOT NULL,
    xml_payload             TEXT NOT NULL,
    csv_payload             TEXT NOT NULL,
    created_at_utc          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (filing_key, version_number)
);

CREATE INDEX IF NOT EXISTS idx_reports_filing_key ON regulatory_reports (filing_key, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_reports_snapshot_id ON regulatory_reports (snapshot_id);

CREATE TABLE IF NOT EXISTS approval_audit_journal (
    audit_id            BIGSERIAL PRIMARY KEY,
    report_id           TEXT NOT NULL REFERENCES regulatory_reports(report_id),
    from_status         TEXT NOT NULL,
    to_status           TEXT NOT NULL,
    actor               TEXT NOT NULL,
    rationale           TEXT NOT NULL,
    event_sha256        CHAR(64) NOT NULL,
    prev_event_sha256   CHAR(64),
    recorded_at_utc     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_report_id ON approval_audit_journal (report_id, audit_id ASC);

-- Immutability guard: prevent updates/deletes on snapshots and audit journal
CREATE OR REPLACE FUNCTION prevent_immutable_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Immutability violation: table % is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_snapshots_immutable ON governed_snapshots;
CREATE TRIGGER trg_snapshots_immutable
    BEFORE UPDATE OR DELETE ON governed_snapshots
    FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();

DROP TRIGGER IF EXISTS trg_audit_immutable ON approval_audit_journal;
CREATE TRIGGER trg_audit_immutable
    BEFORE UPDATE OR DELETE ON approval_audit_journal
    FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
