# Data Contracts and Error Taxonomy

**Schema Version:** `1.0.0`

## 1. Units, Precision, and Rounding Rules

- **Numeric representation**: All monetary amounts, NAVs per unit, share units, and fee rates MUST be supplied as decimal strings (e.g., `"100.000000"`, `"0.02"`) or integers. Binary `float` inputs are rejected (`FLOAT_NOT_ALLOWED`).
- **Currency minor units**: Configured in `config/default_engine_config.json`:
  - `USD`, `EUR`, `GBP`, `CHF`: `2` decimal places (`0.01`)
  - `JPY`: `0` decimal places (`1`)
- **Rounding mode**: `ROUND_HALF_EVEN` (banker's rounding) by default, configurable to `ROUND_HALF_UP`.
- **Per-unit scales**: NAV per unit is formatted/quantized to `6` decimal places (`nav_per_unit_decimals`); investor units to `6` decimal places (`unit_decimals`). Intermediate calculation precision is `28` significant digits.
- **Business dates**: ISO-8601 calendar dates (`YYYY-MM-DD`). No timezone-naive timestamps enter financial day-count calculations. Accrual intervals are half-open `(period_start_date, valuation_date]` with elapsed calendar days `(valuation_date - effective_start).days`.

## 2. Canonical Input Schemas

### 2.1 `FeeAgreement`

| Field | Type | Constraints / Semantics |
|---|---|---|
| `agreement_id` | `str` | Non-empty stable identifier |
| `version` | `int` | Integer `>= 1` |
| `share_class` | `str` | Non-empty share class identifier |
| `currency` | `str` | ISO-4217 code configured in `currency_minor_units` |
| `effective_date` | `YYYY-MM-DD` | Earliest valid valuation date |
| `management_fee_rate` | `Decimal` | Annualized rate in `[0, 1]` (e.g. `"0.02"` for 2%) |
| `management_fee_basis` | `enum` | `BEGINNING_NAV`, `ENDING_PRE_FEE_NAV`, `AVERAGE_NAV` |
| `day_count` | `enum` | `ACT_365`, `ACT_360`, `THIRTY_360`, `MONTHLY_1_12`, `QUARTERLY_1_4` |
| `performance_fee_rate` | `Decimal` | Rate in `[0, 1]` (e.g. `"0.20"` for 20%) |
| `hurdle_type` | `enum` | `NONE`, `HARD_NON_COMPOUNDING`, `HARD_COMPOUNDING`, `SOFT_CATCH_UP` |
| `hurdle_rate` | `Decimal` | Annualized rate in `[0, 1]`; must be `"0"` if `hurdle_type == NONE` |
| `catch_up_rate` | `Decimal` | Required in `(0, 1]` when `hurdle_type == SOFT_CATCH_UP` (default `"1.00"`) |
| `crystallization_frequency` | `enum` | `MONTHLY`, `QUARTERLY`, `ANNUAL`, `CUSTOM_DATES` |
| `crystallization_dates` | `list[YYYY-MM-DD]` | Required non-empty unique dates when `CUSTOM_DATES` |
| `hwm_reset_policy` | `enum` | `ON_CRYSTALLIZATION_GAIN`, `NEVER_RESET_BELOW_HWM`, `PROPORTIONAL_REDEMPTION_ADJUSTMENT` |
| `management_fee_deducted_before_perf` | `bool` | Default `true` |
| `allow_negative_accrual` | `bool` | Default `false` |
| `allow_intra_period_perf_reversal` | `bool` | Default `true` (only reverses existing unrealized accrual; cumulative accrual remains `>= 0`) |

### 2.2 `NavSnapshot`

| Field | Type | Constraints / Semantics |
|---|---|---|
| `snapshot_id` | `str` | Non-empty identifier |
| `period_start_date` | `YYYY-MM-DD` | Prior valuation date / period start (`<= valuation_date`) |
| `valuation_date` | `YYYY-MM-DD` | Current valuation date (`>= agreement.effective_date`) |
| `share_class` | `str` | Must match `FeeAgreement.share_class` |
| `currency` | `str` | Must match `FeeAgreement.currency` |
| `gross_nav_per_unit` | `Decimal` | Gross pre-current-period-management-fee and pre-uncrystallized-performance-fee NAV per unit (`>= 0`) |
| `version` | `int` | Integer `>= 1` |
| `source` | `str` | Provenance label (e.g. `FIXTURE`, `RESTATED_NAV_AUDIT`) |

### 2.3 `InvestorLot`

| Field | Type | Constraints / Semantics |
|---|---|---|
| `lot_id` | `str` | Unique identifier within valuation run |
| `investor_id` | `str` | Investor identifier (synthetic only) |
| `share_class` | `str` | Must match `FeeAgreement.share_class` |
| `series_id` | `str` | Subscription series identifier |
| `subscription_date` | `YYYY-MM-DD` | Must be `<= valuation_date` |
| `units` | `Decimal` | Held units (`>= 0`) |
| `opening_nav_per_unit` | `Decimal` | Opening NAV per unit (`> 0`) |
| `high_water_mark` | `Decimal` | Current per-unit HWM (`> 0`) |
| `hurdle_base_nav` | `Decimal` | Current per-unit hurdle baseline (`> 0`) |
| `prior_accrued_perf_fee` | `Decimal` | Uncrystallized performance fee carried from prior interim period (`>= 0`) |
| `loss_carryforward_amount` | `Decimal` | Dollar loss carryforward below HWM (`>= 0`) |
| `last_crystallization_date` | `YYYY-MM-DD \| null` | Anchor date for current crystallization cycle |

### 2.4 `CapitalFlow`

| Field | Type | Constraints / Semantics |
|---|---|---|
| `flow_id` | `str` | Unique flow identifier |
| `investor_id` | `str` | Investor identifier |
| `lot_id` | `str` | New lot ID for `SUBSCRIPTION`; existing lot ID for `REDEMPTION` |
| `share_class` | `str` | Must match agreement |
| `flow_date` | `YYYY-MM-DD` | Must lie within `[period_start_date, valuation_date]` |
| `flow_type` | `enum` | `SUBSCRIPTION` or `REDEMPTION` |
| `nav_per_unit` | `Decimal` | Dealing gross NAV per unit (`> 0`) |
| `currency` | `str` | Must match agreement |
| `amount` / `units` | `Decimal \| null` | At least one positive value required; redemption units cannot exceed held lot units |

## 3. Canonical Output Contract (`CalculationRunResult`)

Every calculation run produces a deterministic `CalculationRunResult` containing:

- **Stable identifiers**: `run_id` (`RUN-<YYYYMMDD>-v<version>-<hash10>`), `version`, `supersedes_run_id`, `config_version`, `input_hash`, `output_hash`.
- **`lot_calculations`**: Per-lot and per-redemption-slice `LotFeeCalculation` breakdown (basis NAV, management fee, post-mgmt NAV, hurdle NAV, effective benchmark, eligible gain, performance fee delta, crystallized fee, ending accrued fee, new HWM, ending net NAV, loss carryforward, and audit `notes`).
- **`updated_lots`**: Next-period `InvestorLot` states with incremented versions.
- **`journal_entries`**: Balanced double-entry `JournalEntry` list (`entry_id`, `entry_type`, `supersedes_entry_id`, `lines`).
- **`reconciliation`**: `ReconciliationReport` with 8 boolean checks and exact `Decimal` differences (`0.00`).
- **`diagnostics`**: Counters (`lots_evaluated`, `subscriptions_processed`, `redemptions_processed`, `crystallization_events`, `reversals_processed`, `journal_entry_count`).

## 4. Validation and Domain Error Taxonomy

| Error Class | Code | Trigger |
|---|---|---|
| `ValidationError` | `FLOAT_NOT_ALLOWED` | Binary float passed to a `Decimal` field |
| `ValidationError` | `CONFIG_NOT_FOUND` | Missing engine configuration file |
| `ValidationError` | `INVALID_MGMT_FEE_RATE` | Management fee rate outside `[0, 1]` |
| `ValidationError` | `INVALID_PERF_FEE_RATE` | Performance fee rate outside `[0, 1]` |
| `ValidationError` | `HURDLE_RATE_WITH_NONE_TYPE` | Non-zero `hurdle_rate` when `hurdle_type == NONE` |
| `ValidationError` | `NEGATIVE_NAV` | `gross_nav_per_unit < 0` |
| `ValidationError` | `CURRENCY_MISMATCH` | Snapshot or flow currency differs from agreement |
| `ValidationError` | `SHARE_CLASS_MISMATCH` | Snapshot, lot, or flow share class differs from agreement |
| `ValidationError` | `DUPLICATE_LOT_ID` | Duplicate `lot_id` in valuation inputs |
| `ValidationError` | `REDEMPTION_EXCEEDS_UNITS` | Redemption quantity exceeds available lot units |
| `UnsupportedPolicyError` | `UNSUPPORTED_CURRENCY` | Currency missing from `currency_minor_units` |
| `UnsupportedPolicyError` | `UNSUPPORTED_HURDLE_TYPE` | Unknown hurdle type |
| `UnsupportedPolicyError` | `UNSUPPORTED_DAY_COUNT` | Unknown day-count convention |
| `CalendarBoundaryError` | `VALUATION_BEFORE_PERIOD_START` | `valuation_date < period_start_date` |
| `CalendarBoundaryError` | `SKIPPED_CRYSTALLIZATION_BOUNDARY` | Period silently skips an intervening crystallization date |
| `CalendarBoundaryError` | `EMPTY_CUSTOM_CRYSTALLIZATION_DATES` | `CUSTOM_DATES` frequency with empty date list |
| `StorageError` | `DUPLICATE_RUN` | Duplicate `(agreement_id, valuation_date, version)` |
| `ReconciliationError` | `RECONCILIATION_FAILED` | Any double-entry, fee-sum, or NAV-bridge check fails |
| `ApprovalError` | `INVALID_APPROVAL_TRANSITION` | Illegal state transition (e.g., approving an already approved/superseded run) |
