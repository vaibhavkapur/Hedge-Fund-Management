# ADR-004: Role Separation (Maker-Checker) and SHA-256 Hash-Chained Audit Log

- **Status**: Accepted
- **Date**: 2026-11-27
- **Context**: Fund operational controls require strict segregation of duties so no single operator—nor a system administrator—can unilaterally approve financial overrides, close sign-offs, or report publications, and every control attempt (including denials) must be tamper-evident.
- **Decision**: Enforce an explicit `ROLE_PERMISSIONS` matrix plus four-eyes `maker_id != actor_id` validation inside `AuthService.authorize`. Log every successful mutation, failed validation, and denied authorization into an append-only `audit_log` table chained via `entry_hash = sha256(entry_fields + prev_hash)`. Verify both sequence continuity, hash links, and SQL column-vs-JSON consistency in `AuditService.verify_chain()`.
- **Consequences**: Unauthorized role actions and self-approvals are deterministically blocked and permanently recorded; any out-of-band tampering with historical audit rows triggers `AuditIntegrityError`.
