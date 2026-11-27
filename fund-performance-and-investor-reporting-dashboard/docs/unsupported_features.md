# Explicit List of Unsupported Features & Non-Goals

Per `plan.md` Sections 1, 7, and 8, the following capabilities are intentionally **out of scope / unsupported** in this MVP:

1. **No Unaudited Live Performance Claims:** The system refuses to label `SIMULATED` or `PAPER` funds as `AUDITED`, and every report embeds mandatory environment and audit-status disclosures.
2. **No Invented Track Record:** All bundled fixtures are deterministic synthetic datasets generated from a fixed seed (`20260101`) and documented in `data/fixtures/manifest.json`.
3. **No Live Order Execution or Broker Connectivity:** `order_execution_enabled` is permanently `false`; no execution gateway, broker API keys, or order-routing paths exist.
4. **No Direct Code Copying from External Personal Repositories:** Integrations with `08-quantum-risk-engine`, `02-hybrid-quantum-portfolio-optimizer`, and `09-transaction-cost-analysis` are represented by clean typed adapter contracts (`src/fund_reporting/adapters/contracts.py`) pending formal IP, licensing, and pinned-commit security review.
5. **No Binary PDF Rendering Engine:** Report export delivers print-ready self-contained HTML (convertible to PDF via browser print/headless Chromium), JSON, CSV, and Markdown without requiring native C/PDF runtime binaries (`weasyprint`/`wkhtmltopdf`).
6. **No Enterprise SSO / OIDC / Multi-Tenant Key Management:** Local token authentication (`TokenAuthenticator`) demonstrates RBAC and separation of duties; production deployment requires plugging in approved SSO/OIDC and secrets management.
7. **No Automated Administrator Feed Connectors:** NAV, cash flow, benchmark, and FX ingestion is exposed via validated REST/service contracts; live prime-broker/administrator SFTP/FIX parsers are deferred until SAAT asset classes and data vendors are confirmed.
8. **No Multi-Class Waterfall / Equalization Share Accounting:** Fee accrual is modeled at the fund level with high-water mark and hurdle rate; series-of-shares / investor-level equalization accounting is deferred to post-MVP administrator integration.
