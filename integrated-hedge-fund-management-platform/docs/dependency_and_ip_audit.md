# Dependency, Existing Foundations & IP Audit

## 1. Linked Repositories Reviewed Against `plan.md` Section 2

`plan.md` identifies three personal repositories as candidate foundations and explicitly instructs:

> *"Confirm access, licenses, background IP and SAAT ownership terms before copying or adapting any personal repository. A linked prototype is not proof of production readiness. If access or licensing is unresolved, implement against clean, documented contracts instead."*

| Referenced Repository | Intended Integration Role | Current Access / IP Status | Implementation Decision |
|---|---|---|---|
| `Trading-Software/projects/13-broker-reliability-reconciliation` | Prime broker reconciliation & reliability checks | **Unresolved** (no local checkout; SAAT background IP / license clearance pending) | Implemented clean-room `BrokerFeedAdapter` contract (`src/hf_platform/adapters/interfaces.py`) + deterministic fixture adapter. |
| `High-Frequency-Trading-Engine` | Execution fill feed / order management | **Unresolved** (no local checkout; live execution also out-of-scope by policy) | Implemented read-only `ExecutionFeedAdapter` contract; order submission hard-blocked via `ExecutionDisabledError`. |
| `Personal-Finance-Agents/projects/Personal-Trading-Desk` | Portfolio valuation / market data interface | **Unresolved** (no local checkout; IP/license clearance pending) | Implemented clean-room `MarketDataAdapter` and `PortfolioRiskService` with exact `Decimal` double-entry reconciliation. |

## 2. Related Platform Plans (#9, #10, #12, #21, #24, #25)

The implementation plan identifies data storage, portfolio management, performance attribution, investor reporting, disaster recovery, and observability as related projects. The platform exposes versioned JSON contracts (`schema_version = "1.0.0"`) and structured events through its JSONL outbox. The [investor reporting dashboard](../../fund-performance-and-investor-reporting-dashboard/) runs independently with its own fixtures.

## 3. Data Licensing & Entitlements

- All fixtures under `data/fixtures/` are **100% synthetic** (`SYNTHETIC_INTERNAL_TEST_DATA`), documented in `config/fixtures_manifest.json` with fixed seeds and explicit `contains_pii = false`, `contains_production_broker_data = false`, `contains_vendor_market_data = false`.
- No secrets, API keys, broker credentials, or proprietary vendor data exist anywhere in the repository.

## 4. Python Package Dependencies

- **Runtime**: `pydantic>=2.7.0,<3.0.0` (MIT License) + Python 3.12 Standard Library (`sqlite3`, `decimal`, `hashlib`, `http.server`).
- **Development / Validation**: `pytest` (MIT), `ruff` (MIT).
- Reproducible lockfile committed as `uv.lock`.
