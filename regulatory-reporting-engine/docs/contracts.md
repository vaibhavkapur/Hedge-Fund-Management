# Data Contracts — Regulatory Reporting Engine

## 1. Canonical Input Contract (`GovernedSnapshot`, contract version `1.0.0`)

All monetary quantities **must** be serialized as exact base-10 decimal strings (e.g., `"100000000.00"`). JSON floating-point numbers are rejected (`REG-1004`).

### Top-Level Structure

| Field | Type | Required | Semantics / Constraints |
|---|---|---|---|
| `snapshot_id` | string | Yes | `^[A-Z0-9_-]{3,64}$`; immutable unique ID |
| `schema_contract_version` | string | Yes | Supported: `"1.0.0"` |
| `captured_at_utc` | string | Yes | ISO-8601 UTC timestamp ending with `Z` (e.g., `2026-04-01T00:05:00Z`) |
| `provenance` | object | Yes | Source system metadata (`source_system`, `synthetic_data`, optional `seed`) |
| `fund` | object | Yes | Fund identity, LEIs, base currency, domicile, optional liquidity bucket |
| `reporting_period` | object | Yes | `period_id`, `start_date`, `end_date` (`YYYY-MM-DD`, `end_date >= start_date`), `frequency` |
| `nav` | object | Yes | `as_of_date == reporting_period.end_date`; `currency == fund.base_currency`; `GAV - Liabilities == NAV` |
| `positions` | array | Yes | Zero or more `PositionRecord` entries with unique `position_id` |
| `exposures` | array | Yes | Zero or more `ExposureRecord` entries with unique `exposure_id` |

### Identifier Standards

| Identifier | Standard | Validation Rule |
|---|---|---|
| `fund_lei`, `manager_lei`, `counterparty_lei` | ISO 17442 | 20 chars `[A-Z0-9]{18}[0-9]{2}` with ISO 7064 Mod 97-10 remainder == `1` |
| `instrument_isin` | ISO 6166 | 12 chars `[A-Z]{2}[A-Z0-9]{9}[0-9]` with valid Luhn check digit |
| `base_currency`, `currency`, `local_currency` | ISO 4217 | Supported 3-letter currency code (e.g., `USD`, `EUR`, `GBP`, `JPY`, `CHF`) |
| `domicile_country` | ISO 3166-1 alpha-2 | Supported 2-letter country code |

### Accounting & Unit Invariants

- **NAV Identity:** `gross_asset_value - total_liabilities == net_asset_value` (exact `Decimal` equality).
- **Position FX Identity:** `|quantize(quantity * price_local * fx_rate_to_base, 0.01) - market_value_base| <= 0.01`; `LONG` positions must have `market_value_base >= 0`; `SHORT` positions must have `market_value_base <= 0`.
- **Exposure Identities:** `long_exposure_base >= 0`, `short_exposure_base >= 0`, `long + short == gross`, `long - short == net`.
- **Minor Units:** Helper `to_minor_units(amount, scale=100)` converts exact `Decimal` USD/EUR amounts into integer cents without precision loss.

---

## 2. Profile Output Contracts

| Profile ID | Schema Version | Jurisdiction | Effective Date | Fields | Certified? |
|---|---|---|---|---|---|
| `US_SEC_PF_ILLUSTRATIVE` | `1.0.0` | US | `2025-01-01` | 16 | `false` (illustrative) |
| `US_SEC_PF_ILLUSTRATIVE` | `2.0.0` | US | `2026-01-01` | 18 | `false` (illustrative) |
| `EU_AIFMD_ANNEX4_ILLUSTRATIVE` | `1.0.0` | EU | `2025-01-01` | 13 | `false` (illustrative) |

### Output Artifacts per Generated Report

1. **XML Draft (`*.xml`):** Root element bound to the profile's versioned URN namespace, `<Header>` carrying `reportId`, `filingKey`, `versionNumber`, `snapshotId`, `schemaVersion`, `schemaEffectiveDate`, `certifiedForFiling="false"`, and `watermark`, followed by `<ReportBody>` in exact schema field order with `unit` attributes on quantitative elements.
2. **CSV Draft (`*.csv`):** Single-row canonical table with leading metadata columns (`report_id`, `version_number`, `profile_id`, `schema_version`, `watermark`) followed by profile fields in exact schema declaration order.
3. **Evidence Lineage Bundle (`*.lineage.json`):** Array of `FieldLineage` entries recording `target_field`, `value`, `unit`, `source_type`, `mapping_id`, `mapping_version`, `snapshot_id`, `source_record_ids`, `derivation_expression`, `upstream_fields`, `rounding_mode`, and `decimal_places`.
4. **Deterministic Report ID:** `RPT-<fund_id>-<period_id>-<profile_id>-V<version>-<content_sha256[:12].upper()>`.

---

## 3. Validation Error Catalog

| Code | Exception Class | Trigger Condition |
|---|---|---|
| `REG-1001` | `ConfigurationError` | Missing/malformed config file, unsupported rounding mode or storage backend |
| `REG-1002` | `MissingRequiredDataError` | Required snapshot, fund, period, NAV, or mapped source field absent |
| `REG-1003` | `InvalidIdentifierError` | Invalid LEI check digit, ISIN Luhn digit, ISO currency/country, date, or duplicate record ID |
| `REG-1004` | `UnitMismatchError` | Float input, currency mismatch, date mismatch, or NAV/position/exposure identity violation |
| `REG-1005` | `UnsupportedInputError` | Unknown profile, contract version, asset class, frequency, or aggregation primitive |
| `REG-1006` | `PostgresJournalStoreUnavailableError` | `postgres` backend selected without live database/driver |
| `REG-2001` | `SchemaEvolutionError` | Mapping target schema version or field set incompatible with requested `ProfileSchema` |
| `REG-2002` | `SchemaValidationError` | Fields, rendered XML, or rendered CSV fail type, scale, bound, enum, or structural checks |
| `REG-2003` | `MappingError` | Invalid mapping path, unresolved derived dependency, or incomplete lineage |
| `REG-3001` | `CrossReportReconciliationError` | Cross-report NAV/exposure/leverage/count mismatch or internal schedule additivity break |
| `REG-4001` | `DuplicateFilingError` | Duplicate v1 filing for an existing `(fund_id, period_id, profile_id)` key |
| `REG-4002` | `ApprovalWorkflowError` | Illegal status transition (e.g., `DRAFT -> APPROVED` skipping `REVIEWED`) or correcting a superseded version |
| `REG-4003` | `ImmutabilityViolationError` | Attempted mutation/deletion of snapshots, report content, or approval audit journal |
| `REG-9001` | `AutomatedSubmissionProhibitedError` | Attempting to enable automated submission in config or invoke `submit_to_regulator()` |

---

## 4. Fixture Inventory (`data/fixtures/`)

All fixtures are 100% synthetic (zero real entities, transactions, or credentials):

| Fixture File | Category | Purpose |
|---|---|---|
| `reference_snapshot_q1_2026.json` | Valid reference | Primary deterministic Q1 2026 synthetic fund ($100M NAV, 4 positions, 3 exposure buckets) |
| `reference_snapshot_q1_2026_corrected.json` | Valid correction | Restated Q1 2026 snapshot (+$1.5M NAV accrual) for v1->v2 correction & mismatch tests |
| `reference_expected.json` | Independent oracle | Hand-calculated exact expected field outputs for all 3 profiles |
| `invalid_missing_required.json` | Invalid (missing data) | Omits `fund.manager_lei` and `nav.net_asset_value` |
| `invalid_identifier.json` | Invalid (bad IDs) | Corrupt LEI check digit (`...99`) and corrupt ISIN Luhn digit (`...19`) |
| `boundary_zero_nav_empty_positions.json` | Boundary | Dormant fund with `0.00` NAV, 0 positions, 0 exposures (tests zero-denominator safety) |
| `nav_engine_sample_output.json` | Adapter fixture | Sample payload shaped like `fund-nav-calculation-engine` output |
