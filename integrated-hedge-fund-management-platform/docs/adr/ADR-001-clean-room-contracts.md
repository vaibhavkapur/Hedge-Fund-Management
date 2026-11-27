# ADR-001: Clean-Room Adapter Contracts for Unresolved External Repositories

- **Status**: Accepted
- **Date**: 2026-11-27
- **Context**: `plan.md` Section 2 references three personal GitHub repositories (`13-broker-reliability-reconciliation`, `High-Frequency-Trading-Engine`, `Personal-Trading-Desk`) and requires confirming access, licenses, background IP, and SAAT ownership terms before copying or adapting code. Those repositories are not checked out locally and IP clearance is pending.
- **Decision**: Define clean-room abstract adapter contracts (`BrokerFeedAdapter`, `ExecutionFeedAdapter`, `MarketDataAdapter`, `FundAdminAdapter`, `EventPublisherAdapter`) in `src/hf_platform/adapters/interfaces.py` and implement deterministic synthetic fixture adapters with fault injection (`fixture_adapters.py`). Keep order execution strictly disabled by policy.
- **Consequences**: Zero IP/licensing contamination; full deterministic testability; future production or cleared personal adapters can plug in without modifying core accounting, risk, or close orchestration logic.
