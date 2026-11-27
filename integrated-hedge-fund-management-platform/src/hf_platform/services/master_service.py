"""Fund, account, strategy, instrument, counterparty, and user master service."""

from __future__ import annotations

import json
from pathlib import Path

from hf_platform.domain import (
    Account,
    Counterparty,
    EventType,
    Fund,
    Instrument,
    MasterDataBundle,
    Role,
    Strategy,
    UserPrincipal,
)
from hf_platform.errors import DomainValidationError, NotFoundError
from hf_platform.services.audit_service import AuditService
from hf_platform.storage.repository import StorageRepository


class MasterDataService:
    """Owns canonical master data contracts and validates referential integrity."""

    def __init__(self, store: StorageRepository, audit: AuditService) -> None:
        self._store = store
        self._audit = audit

    def load_bundle_file(self, path: str | Path, actor_id: str = "usr_sys_01") -> MasterDataBundle:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        bundle = MasterDataBundle.model_validate(raw)
        self.register_bundle(bundle, actor_id=actor_id)
        return bundle

    def register_bundle(self, bundle: MasterDataBundle, actor_id: str = "usr_sys_01") -> None:
        fund_ids = {f.fund_id for f in bundle.funds}
        cp_ids = {c.counterparty_id for c in bundle.counterparties}
        user_ids = {u.user_id for u in bundle.users}

        for acct in bundle.accounts:
            if acct.fund_id not in fund_ids:
                raise DomainValidationError(
                    f"Account {acct.account_id} references unknown fund {acct.fund_id}"
                )
            if acct.counterparty_id not in cp_ids:
                raise DomainValidationError(
                    f"Account {acct.account_id} references unknown counterparty {acct.counterparty_id}"
                )

        for strat in bundle.strategies:
            if strat.fund_id not in fund_ids:
                raise DomainValidationError(
                    f"Strategy {strat.strategy_id} references unknown fund {strat.fund_id}"
                )
            if strat.pm_user_id not in user_ids:
                raise DomainValidationError(
                    f"Strategy {strat.strategy_id} references unknown PM user {strat.pm_user_id}"
                )

        for user in bundle.users:
            self._store.save_user(user)
        for cp in bundle.counterparties:
            self._store.save_counterparty(cp)
        for fund in bundle.funds:
            self._store.save_fund(fund)
        for acct in bundle.accounts:
            self._store.save_account(acct)
        for strat in bundle.strategies:
            self._store.save_strategy(strat)
        for inst in bundle.instruments:
            self._store.save_instrument(inst)

        actor = self._store.get_user(actor_id)
        self._audit.record(
            actor_id=actor_id,
            actor_role=actor.role if actor else Role.SYSTEM_ADMIN,
            action="master.load_bundle",
            entity_type="master_bundle",
            entity_id=bundle.schema_version,
            details={
                "funds": len(bundle.funds),
                "accounts": len(bundle.accounts),
                "strategies": len(bundle.strategies),
                "instruments": len(bundle.instruments),
                "users": len(bundle.users),
            },
        )
        self._audit.emit_event(
            event_type=EventType.MASTER_DATA_UPDATED,
            payload={"schema_version": bundle.schema_version, "fund_ids": sorted(fund_ids)},
        )

    def get_fund(self, fund_id: str) -> Fund:
        fund = self._store.get_fund(fund_id)
        if fund is None:
            raise NotFoundError(f"Fund not found: {fund_id}", details={"fund_id": fund_id})
        return fund

    def list_funds(self) -> list[Fund]:
        return self._store.list_funds()

    def list_accounts(self, fund_id: str | None = None) -> list[Account]:
        return self._store.list_accounts(fund_id=fund_id)

    def list_strategies(self, fund_id: str | None = None) -> list[Strategy]:
        return self._store.list_strategies(fund_id=fund_id)

    def list_instruments(self) -> list[Instrument]:
        return self._store.list_instruments()

    def list_counterparties(self) -> list[Counterparty]:
        return self._store.list_counterparties()

    def list_users(self) -> list[UserPrincipal]:
        return self._store.list_users()

    def instrument_map(self) -> dict[str, Instrument]:
        return {inst.instrument_id: inst for inst in self._store.list_instruments()}

    def account_map(self, fund_id: str) -> dict[str, Account]:
        return {acct.account_id: acct for acct in self._store.list_accounts(fund_id=fund_id)}

    def strategy_map(self, fund_id: str) -> dict[str, Strategy]:
        return {strat.strategy_id: strat for strat in self._store.list_strategies(fund_id=fund_id)}

    def counterparty_map(self) -> dict[str, Counterparty]:
        return {cp.counterparty_id: cp for cp in self._store.list_counterparties()}
