# Data Contracts — Investor Subscription and Redemption Platform

**Contract Schema Version:** `1.0.0`

## 1. Units, Precision, Currency, and Timestamp Semantics

- **Monetary Amounts (`gross_amount`, `fee_amount`, `net_cash`, `amount`):** Decimal strings quantized to `2` decimal places (`ROUND_HALF_UP`), paired with explicit ISO-4217 currency code (`USD`, `EUR`, `GBP`). Floats are rejected (`VALIDATION_ERROR`).
- **Share Quantities (`requested_shares`, `calculated_shares`, `share_delta`):** Decimal strings quantized to `6` decimal places (`ROUND_HALF_UP`).
- **NAV (`nav_per_share`):** Strictly positive decimal strings quantized to `6` decimal places (`ROUND_HALF_UP`).
- **Fee Rates (`subscription_fee_bps`, `redemption_fee_bps`):** Non-negative integer basis points (`0..5000`, where `100 bps = 1.00%`).
- **Business Dates (`dealing_date`, `cash_due_date`, `settlement_date`, `value_date`):** ISO-8601 `YYYY-MM-DD`.
- **Timestamps (`open_at`, `cutoff_at`, `submitted_at`, `approved_at`, `received_at`, `timestamp`):** ISO-8601 strings with explicit timezone offset (normalized to UTC `Z`). Naive timestamps without timezone offsets are rejected (`VALIDATION_ERROR`).

## 2. Canonical Inputs

### Synthetic Investor Record (`POST /investors`)
```json
{
  "investor_id": "INV-SYN-001",
  "legal_name": "Meridian Synthetic Endowment Trust",
  "investor_type": "INSTITUTIONAL",
  "jurisdiction": "US",
  "tax_form_status": "VERIFIED",
  "accredited": true,
  "bank_account_ref": "SYN-ACCT-1001",
  "identity_doc_ref": "SYN-DOC-9001"
}
```
> All investor records are synthetic. Mock onboarding checks (`SYN-DOC-*`, `SYN-ACCT-*`, sanctions list, `VERIFIED` tax form, `accredited=true`) are educational simulations only.

### Dealing Window (`POST /dealing-windows`)
Fields: `window_id`, `fund_id`, `share_class`, `dealing_date`, `open_at`, `cutoff_at`, `cash_due_date`, `settlement_date`. Invariants: `open_at < cutoff_at`, `cash_due_date >= dealing_date`, `settlement_date >= dealing_date`.

### Approved NAV (`POST /navs` and `POST /navs/restate`)
Fields: `nav_id`, `fund_id`, `share_class`, `dealing_date`, `currency`, `nav_per_share`, `version` (starts at `1`), `approved_by`, `approved_at`. Restatements require `maker_id != checker_id` and non-empty `reason`.

### Capital Flow Request (`POST /requests`)
Fields: `request_id`, `idempotency_key` (unique), `investor_id`, `fund_id`, `share_class`, `request_type` (`SUBSCRIPTION` | `REDEMPTION`), `dealing_date`, `currency`, `gross_amount` (required for subscriptions; optional for cash-based redemptions), `requested_shares` (for share-based redemptions), `submitted_at`, `maker_id`.

### Cash Confirmation (`POST /cash-confirmations`)
Fields: `confirmation_id` (unique), `request_id`, `direction` (`INBOUND` | `OUTBOUND`), `currency`, `amount`, `value_date`, `bank_reference`, `received_at`.

## 3. Canonical Outputs

- **Share Register (`GET /share-register`):** Per `(investor_id, fund_id, share_class, currency)` snapshot containing `settled_shares`, `pending_redemption_shares`, `available_shares`, `total_subscribed_gross`, `total_redeemed_net`, `total_fees_paid`.
- **Reconciliation Report (`GET /reconciliation`):** Boolean `reconciled`, `total_register_shares`, `total_journal_shares`, `share_discrepancy`, inbound/outbound confirmed vs. journal cash totals and discrepancies, `total_fees_collected`, `unapproved_settled_count`, `negative_balance_positions`, `discrepancies`.
- **Audit Trail (`GET /audit`):** Boolean `valid`, `errors`, and ordered `AuditEvent` array with `sequence`, `event_id`, `prev_hash`, `entry_hash`.
- **Deterministic Snapshot (`PlatformService.export_state_snapshot()`):** Complete logical state plus `config_hash` and `logical_state_hash` (SHA-256).

## 4. Error Contract

All domain and validation errors return structured JSON:
```json
{
  "error_code": "REVIEWER_SEPARATION_VIOLATION",
  "message": "Maker-checker violation during request_review: actor 'ops_maker_01' cannot review/approve their own action.",
  "details": {
    "maker_id": "ops_maker_01",
    "checker_id": "ops_maker_01",
    "context": "request_review"
  }
}
```

| Error Class | `error_code` | HTTP Status | Trigger |
|---|---|---:|---|
| `ConfigurationError` | `CONFIGURATION_ERROR` | 500 | Missing/corrupt config, unknown fund/share class, invalid rounding mode |
| `ValidationError` | `VALIDATION_ERROR` | 422 | Float inputs, naive timestamps, negative/below-minimum amounts, unsupported currency, inactive KYC |
| `NotFoundError` | `NOT_FOUND` | 404 | Unknown investor, window, NAV, request, or exception ID |
| `DuplicateRequestError` | `DUPLICATE_REQUEST` | 409 | Repeated `idempotency_key`, `request_id`, `confirmation_id`, or duplicate NAV version |
| `CutoffViolationError` | `CUTOFF_VIOLATION` | 422 | `submitted_at < open_at` or `submitted_at > cutoff_at` |
| `InsufficientSharesError` | `INSUFFICIENT_SHARES` | 422 | Redemption exceeds `available_shares` (settled minus pending redemption locks) |
| `ReviewerSeparationError` | `REVIEWER_SEPARATION_VIOLATION` | 403 | `checker_id == maker_id` or `reviewer_id == created_by` |
| `UnapprovedRequestError` | `UNAPPROVED_REQUEST` | 409 | Pricing or settlement attempted without valid maker-checker approval |
| `InvalidStateTransitionError` | `INVALID_STATE_TRANSITION` | 409 | Disallowed investor/request/exception transition (e.g., cancelling a settled request) |
| `ReconciliationError` | `RECONCILIATION_ERROR` | 409 | Unresolved open exceptions or unmatched/insufficient cash at settlement |

## 5. Reference & Boundary Fixtures

- `data/fixtures/reference_oracle.json`: Independent hand-calculated baseline covering exact subscription (`792.000000` shares), rounded subscription (`371.259281` shares with sub-cent residual), redemption (`26532.67` net payout), and NAV restatement (`+8.000000` share delta).
- `data/fixtures/valid_scenario.json`: Multi-investor, multi-window end-to-end subscription and redemption fixture with expected register and cash reconciliation totals.
- `data/fixtures/edge_and_failure_scenarios.json`: Deterministic cases for duplicate request, delayed cash, cutoff violation, insufficient shares, NAV restatement, reviewer separation, sanctioned investor, and invalid inputs.
