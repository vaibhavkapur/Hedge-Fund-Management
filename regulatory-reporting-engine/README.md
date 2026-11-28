# Regulatory Reporting Engine

Configurable illustrative regulatory-style reporting prototype that transforms governed fund snapshots into schema-validated XML/CSV draft reports with 100% field-level evidence lineage, cross-report reconciliation, immutable snapshotting, versioned corrections, and cryptographically chained approval audits.

> **Important Disclaimer / Out of Scope:** This is an independently developed educational and research prototype using exclusively synthetic data. It is **not** a certified regulatory filing system, **does not** implement official SEC Form PF or ESMA AIFMD XML schemas, and **structurally prohibits automated external submission** (`automated_submission_enabled=false`, enforced in both configuration loading and runtime API). Verify actual filing obligations and official schemas with qualified legal and compliance staff before any production use.

---

## Core Capabilities

- **Jurisdiction/Profile Example Schemas with Dated Provenance:** Versioned illustrative profiles in `config/profiles/` (`US_SEC_PF_ILLUSTRATIVE:1.0.0`, `US_SEC_PF_ILLUSTRATIVE:2.0.0`, `EU_AIFMD_ANNEX4_ILLUSTRATIVE:1.0.0`) and versioned declarative mappings in `config/mappings/`.
- **Exact Numeric & Identifier Governance:** Pure `Decimal` arithmetic (rejecting floating-point inputs), explicit currency/business-date/rounding rules (`ROUND_HALF_UP`), minor-unit conversion, and ISO 17442 LEI (Mod-97-10), ISO 6166 ISIN (Luhn), ISO 4217 currency, and ISO 3166 country validation.
- **100% Field-Level Evidence Lineage:** Every output field traces deterministically to source snapshot record IDs (e.g., `POS-001`, `EXP-001`, `nav:NAV-SYNTH-2026-03-31-V1`) or declared derivation expressions referencing upstream fields.
- **XML & CSV Schema Validation:** Round-trip validation of namespaces, root elements, field order, types, decimal scales, bounds, enums, and mandatory non-certified watermarks.
- **Cross-Report Reconciliation:** Automated cross-profile consistency checks (NAV, gross exposure, net exposure, leverage equivalence, position count, and internal asset-class additivity).
- **Immutable Journal, Versioned Corrections & Approval Audit:** Append-only SQLite store (mirroring `sql/schema_postgres.sql`) with database-level immutability triggers, duplicate-filing rejection, `DRAFT -> REVIEWED -> APPROVED -> SUPERSEDED` lifecycle enforcement, schema-evolution migration (`1.0.0 -> 2.0.0`), and SHA-256 hash-chained audit entries.

---

## Quickstart & Setup

### Requirements

- **Python 3.9+** (zero mandatory third-party runtime dependencies; uses Python standard library `decimal`, `sqlite3`, `xml.etree.ElementTree`, `csv`, `hashlib`, `unittest`).
- Optional extras declared in `pyproject.toml`: `pydantic`, `psycopg[binary]`, `xmlschema`, `lxml`, `pytest`, `mypy`, `ruff`.

### 1. Run the Full Test Suite

```bash
cd regulatory-reporting-engine
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

### 2. Run the End-to-End Reproducible Demo

```bash
python3 scripts/run_demo.py
```

This executes the full lifecycle on synthetic Q1 2026 fixtures (`reference_snapshot_q1_2026.json` and `reference_snapshot_q1_2026_corrected.json`), generating XML, CSV, and lineage artifacts under `reports/demo_outputs/`.

### 3. Run Acceptance Checks & Micro-Benchmark

```bash
python3 scripts/run_acceptance.py
```

### 4. Use the CLI Directly

```bash
PYTHONPATH=src python3 -m regulatory_reporting_engine.cli list-profiles
PYTHONPATH=src python3 -m regulatory_reporting_engine.cli validate-snapshot data/fixtures/reference_snapshot_q1_2026.json
PYTHONPATH=src python3 -m regulatory_reporting_engine.cli schema-diff --profile US_SEC_PF_ILLUSTRATIVE --from-version 1.0.0 --to-version 2.0.0
```

---

## Example Output (Synthetic Q1 2026 Reference Fixture)

Generated `reports/demo_outputs/us_pf_v1_draft.xml`:

```xml
<?xml version='1.0' encoding='utf-8'?>
<IllustrativePrivateFundReport xmlns="urn:synthetic:regulatory:us-pf-illustrative:v1">
  <Header reportId="RPT-SYNTH_ALPHA_01-2026-Q1-US_SEC_PF_ILLUSTRATIVE-V1-E28146B3FA3E"
          filingKey="SYNTH_ALPHA_01::2026-Q1::US_SEC_PF_ILLUSTRATIVE"
          versionNumber="1"
          snapshotId="SNAP-SYNTH-2026Q1-0001"
          profileId="US_SEC_PF_ILLUSTRATIVE"
          schemaVersion="1.0.0"
          schemaEffectiveDate="2025-01-01"
          mappingId="US_SEC_PF_ILLUSTRATIVE_MAP"
          mappingVersion="1.0.0"
          certifiedForFiling="false"
          watermark="ILLUSTRATIVE_DRAFT_NOT_FOR_REGULATORY_FILING" />
  <ReportBody>
    <FundIdentifier>SYNTH_ALPHA_01</FundIdentifier>
    <FundLEI>000000SYNTHFUND00181</FundLEI>
    ...
    <NetAssetValueUSD unit="USD">100000000.00</NetAssetValueUSD>
    <GrossExposureUSD unit="USD">150000000.00</GrossExposureUSD>
    <GrossLeverageRatio unit="ratio">1.500000</GrossLeverageRatio>
    <TopCounterpartyExposureUSD unit="USD">105000000.00</TopCounterpartyExposureUSD>
  </ReportBody>
</IllustrativePrivateFundReport>
```

---

## Measured Results vs. Synthetic Fixtures

All measurements below were observed on **Python 3.9.6 / SQLite 3.51.0 / macOS arm64** using **synthetic fixtures** (`data/fixtures/reference_snapshot_q1_2026.json`, 4 positions, 3 exposure buckets); they are not production scale estimates or guarantees:

- **Automated Test Suite:** 31 tests run, 31 passed, 0 failures, 0 errors, 0 skipped (`0.053 s`).
- **Hand-Calculated Oracle Comparison:** Exact `0.00` deviation across all 3 profiles (`16/16` fields on `US_SEC_PF_ILLUSTRATIVE:1.0.0`, `18/18` fields on `US_SEC_PF_ILLUSTRATIVE:2.0.0`, `13/13` fields on `EU_AIFMD_ANNEX4_ILLUSTRATIVE:1.0.0`) with `100%` lineage coverage.
- **Micro-Benchmark (200 iterations, dual-profile mapping + XML/CSV validation + reconciliation):** mean `0.3820 ms`, p50 `0.3775 ms`, p95 `0.4126 ms`, p99 `0.4535 ms`, throughput `2,618.04 bundles/sec`.
- **Untested / Not Run Capabilities:** Live PostgreSQL server execution (`psycopg` + Postgres instance) was **not run** in the offline environment; schema parity is provided via `sql/schema_postgres.sql` and verified against SQLite triggers.

---

## Documentation & Evidence Index

| Artifact | Purpose |
|---|---|
| [plan.md](plan.md) | Implementation plan, milestones M1–M4, and acceptance criteria |
| [docs/architecture.md](docs/architecture.md) | Component diagram, data flow, layer boundaries, invariants, and trade-offs |
| [docs/adr/0001-architecture-and-stack-decisions.md](docs/adr/0001-architecture-and-stack-decisions.md) | ADR-0001 documenting stack choices, zero-dependency core, and Postgres/SQLite parity |
| [docs/contracts.md](docs/contracts.md) | Input/output schemas, units, timestamps, ISO identifiers, errors, and fixtures |
| [docs/integration.md](docs/integration.md) | Adapter boundaries with `Trading-Management` sibling projects and optional adapters |
| [docs/runbook.md](docs/runbook.md) | Setup, demo, validation, diagnostics, failure recovery, and CI workflow |
| [reports/acceptance.md](reports/acceptance.md) | Acceptance criteria & test matrix mapped to measured test and benchmark evidence |
| [reports/run_metadata.json](reports/run_metadata.json) | Machine-readable test, oracle, demo, and benchmark evidence with runtime metadata |

---

## Assumptions, Limitations & License Provenance

- **Synthetic Data Only:** All LEIs (`000000SYNTH...`), ISINs (`US0000000010`, `DE0000000017`, etc.), funds, and monetary amounts are synthetic and mathematically valid for format/check-digit testing only.
- **No Real Transactions or Proprietary Artifacts:** Zero real customer records, credentials, or employer/SAAT code.
- **Dependency Licenses:** Core runtime uses Python Standard Library only (PSF License). Project code is MIT-licensed. Optional extras (`pydantic` MIT, `psycopg` LGPL-3.0, `xmlschema` MIT, `lxml` BSD-3-Clause) are documented in `pyproject.toml` and not required for offline execution.
