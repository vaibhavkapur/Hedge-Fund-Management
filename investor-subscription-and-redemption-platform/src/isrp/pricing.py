"""Pure Decimal share issuance, redemption proceeds, fee, and NAV restatement calculations.

Isolated from I/O and storage so all calculations are deterministic and independently verifiable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .config import RoundingConfig, ShareClassConfig
from .errors import ValidationError
from .models import NAVRecord, NAVStatus, PricingCalculation, RequestType

BPS_DENOMINATOR = Decimal("10000")


def quantize_cash(amount: Decimal, rounding: RoundingConfig) -> Decimal:
    return amount.quantize(rounding.cash_quantum, rounding=rounding.decimal_rounding)


def quantize_shares(shares: Decimal, rounding: RoundingConfig) -> Decimal:
    return shares.quantize(rounding.share_quantum, rounding=rounding.decimal_rounding)


def quantize_nav(nav: Decimal, rounding: RoundingConfig) -> Decimal:
    return nav.quantize(rounding.nav_quantum, rounding=rounding.decimal_rounding)


def calculate_subscription(
    *,
    request_id: str,
    gross_cash: Decimal,
    nav: NAVRecord,
    share_class_cfg: ShareClassConfig,
    rounding: RoundingConfig,
) -> PricingCalculation:
    """Calculate entry fee, net investable cash, issued shares, and rounding residual."""
    if nav.status != NAVStatus.APPROVED:
        raise ValidationError(
            f"Cannot price subscription with non-approved NAV '{nav.nav_id}' (status={nav.status.value}).",
            {"nav_id": nav.nav_id, "nav_status": nav.status.value},
        )
    if nav.nav_per_share <= Decimal("0"):
        raise ValidationError("NAV per share must be strictly positive.", {"nav_per_share": str(nav.nav_per_share)})
    if gross_cash <= Decimal("0"):
        raise ValidationError("Subscription gross cash must be strictly positive.", {"gross_cash": str(gross_cash)})
    if nav.currency != share_class_cfg.currency:
        raise ValidationError(
            f"NAV currency '{nav.currency}' does not match share class currency '{share_class_cfg.currency}'.",
            {"nav_currency": nav.currency, "share_class_currency": share_class_cfg.currency},
        )

    gross_q = quantize_cash(gross_cash, rounding)
    fee_rate = Decimal(share_class_cfg.subscription_fee_bps) / BPS_DENOMINATOR
    fee_amount = quantize_cash(gross_q * fee_rate, rounding)
    net_cash = quantize_cash(gross_q - fee_amount, rounding)
    if net_cash <= Decimal("0"):
        raise ValidationError("Net investable cash after fees must be strictly positive.")

    nav_q = quantize_nav(nav.nav_per_share, rounding)
    shares = quantize_shares(net_cash / nav_q, rounding)
    if shares <= Decimal("0"):
        raise ValidationError("Calculated subscription shares must be strictly positive.")

    exact_valuation = shares * nav_q
    valuation_at_nav = quantize_cash(exact_valuation, rounding)
    rounding_residual = net_cash - exact_valuation

    if abs(rounding_residual) > rounding.max_valuation_residual:
        raise ValidationError(
            f"Subscription rounding residual {rounding_residual} exceeds max tolerance {rounding.max_valuation_residual}.",
            {"rounding_residual": str(rounding_residual), "max_tolerance": str(rounding.max_valuation_residual)},
        )

    return PricingCalculation(
        request_id=request_id,
        request_type=RequestType.SUBSCRIPTION,
        nav_id=nav.nav_id,
        nav_version=nav.version,
        nav_per_share=nav_q,
        currency=nav.currency,
        fee_bps=share_class_cfg.subscription_fee_bps,
        gross_cash=gross_q,
        fee_amount=fee_amount,
        net_cash=net_cash,
        shares=shares,
        valuation_at_nav=valuation_at_nav,
        rounding_residual=rounding_residual,
    )


def calculate_redemption(
    *,
    request_id: str,
    requested_shares: Optional[Decimal],
    requested_gross_cash: Optional[Decimal],
    nav: NAVRecord,
    share_class_cfg: ShareClassConfig,
    rounding: RoundingConfig,
) -> PricingCalculation:
    """Calculate redeemed shares, gross proceeds, exit fee, and net payout cash."""
    if nav.status != NAVStatus.APPROVED:
        raise ValidationError(
            f"Cannot price redemption with non-approved NAV '{nav.nav_id}' (status={nav.status.value}).",
            {"nav_id": nav.nav_id, "nav_status": nav.status.value},
        )
    if nav.nav_per_share <= Decimal("0"):
        raise ValidationError("NAV per share must be strictly positive.", {"nav_per_share": str(nav.nav_per_share)})
    if nav.currency != share_class_cfg.currency:
        raise ValidationError(
            f"NAV currency '{nav.currency}' does not match share class currency '{share_class_cfg.currency}'.",
            {"nav_currency": nav.currency, "share_class_currency": share_class_cfg.currency},
        )
    if (requested_shares is None) == (requested_gross_cash is None):
        raise ValidationError(
            "Redemption must specify exactly one of requested_shares or gross_amount.",
            {"requested_shares": str(requested_shares), "gross_amount": str(requested_gross_cash)},
        )

    nav_q = quantize_nav(nav.nav_per_share, rounding)
    if requested_shares is not None:
        if requested_shares <= Decimal("0"):
            raise ValidationError("requested_shares must be strictly positive.")
        shares = quantize_shares(requested_shares, rounding)
        exact_gross = shares * nav_q
        gross_cash = quantize_cash(exact_gross, rounding)
        rounding_residual = gross_cash - exact_gross
    else:
        assert requested_gross_cash is not None
        if requested_gross_cash <= Decimal("0"):
            raise ValidationError("Redemption gross_amount must be strictly positive.")
        gross_cash = quantize_cash(requested_gross_cash, rounding)
        shares = quantize_shares(gross_cash / nav_q, rounding)
        exact_gross = shares * nav_q
        rounding_residual = gross_cash - exact_gross

    if abs(rounding_residual) > rounding.max_valuation_residual:
        raise ValidationError(
            f"Redemption rounding residual {rounding_residual} exceeds max tolerance {rounding.max_valuation_residual}.",
            {"rounding_residual": str(rounding_residual)},
        )

    fee_rate = Decimal(share_class_cfg.redemption_fee_bps) / BPS_DENOMINATOR
    fee_amount = quantize_cash(gross_cash * fee_rate, rounding)
    net_cash = quantize_cash(gross_cash - fee_amount, rounding)
    if net_cash <= Decimal("0"):
        raise ValidationError("Net redemption payout after fees must be strictly positive.")

    return PricingCalculation(
        request_id=request_id,
        request_type=RequestType.REDEMPTION,
        nav_id=nav.nav_id,
        nav_version=nav.version,
        nav_per_share=nav_q,
        currency=nav.currency,
        fee_bps=share_class_cfg.redemption_fee_bps,
        gross_cash=gross_cash,
        fee_amount=fee_amount,
        net_cash=net_cash,
        shares=shares,
        valuation_at_nav=quantize_cash(exact_gross, rounding),
        rounding_residual=rounding_residual,
    )


@dataclass(frozen=True)
class RestatementDelta:
    request_id: str
    request_type: RequestType
    original_nav_id: str
    restated_nav_id: str
    original_shares: Decimal
    restated_shares: Decimal
    share_delta: Decimal
    original_net_cash: Decimal
    restated_net_cash: Decimal
    net_cash_delta: Decimal
    original_fee: Decimal
    restated_fee: Decimal
    fee_delta: Decimal

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "request_type": self.request_type.value,
            "original_nav_id": self.original_nav_id,
            "restated_nav_id": self.restated_nav_id,
            "original_shares": str(self.original_shares),
            "restated_shares": str(self.restated_shares),
            "share_delta": str(self.share_delta),
            "original_net_cash": str(self.original_net_cash),
            "restated_net_cash": str(self.restated_net_cash),
            "net_cash_delta": str(self.net_cash_delta),
            "original_fee": str(self.original_fee),
            "restated_fee": str(self.restated_fee),
            "fee_delta": str(self.fee_delta),
        }


def calculate_nav_restatement_delta(
    *,
    request_id: str,
    request_type: RequestType,
    original_nav_id: str,
    original_gross_cash: Decimal,
    original_fee: Decimal,
    original_net_cash: Decimal,
    original_shares: Decimal,
    restated_nav: NAVRecord,
    share_class_cfg: ShareClassConfig,
    rounding: RoundingConfig,
) -> RestatementDelta:
    """Compute exact adjustment deltas when a dealing date's NAV is restated.

    Policy:
    - Subscription: investor cash contribution is fixed -> recalculate shares at restated NAV;
      share_delta = restated_shares - original_shares, cash_delta = 0.00.
    - Redemption (share-based): redeemed shares are fixed -> recalculate gross/fee/net payout
      at restated NAV; share_delta = 0.000000, net_cash_delta = restated_net - original_net.
    """
    if request_type == RequestType.SUBSCRIPTION:
        recalc = calculate_subscription(
            request_id=request_id,
            gross_cash=original_gross_cash,
            nav=restated_nav,
            share_class_cfg=share_class_cfg,
            rounding=rounding,
        )
        return RestatementDelta(
            request_id=request_id,
            request_type=request_type,
            original_nav_id=original_nav_id,
            restated_nav_id=restated_nav.nav_id,
            original_shares=original_shares,
            restated_shares=recalc.shares,
            share_delta=quantize_shares(recalc.shares - original_shares, rounding),
            original_net_cash=original_net_cash,
            restated_net_cash=recalc.net_cash,
            net_cash_delta=quantize_cash(recalc.net_cash - original_net_cash, rounding),
            original_fee=original_fee,
            restated_fee=recalc.fee_amount,
            fee_delta=quantize_cash(recalc.fee_amount - original_fee, rounding),
        )

    recalc = calculate_redemption(
        request_id=request_id,
        requested_shares=original_shares,
        requested_gross_cash=None,
        nav=restated_nav,
        share_class_cfg=share_class_cfg,
        rounding=rounding,
    )
    return RestatementDelta(
        request_id=request_id,
        request_type=request_type,
        original_nav_id=original_nav_id,
        restated_nav_id=restated_nav.nav_id,
        original_shares=original_shares,
        restated_shares=recalc.shares,
        share_delta=quantize_shares(recalc.shares - original_shares, rounding),
        original_net_cash=original_net_cash,
        restated_net_cash=recalc.net_cash,
        net_cash_delta=quantize_cash(recalc.net_cash - original_net_cash, rounding),
        original_fee=original_fee,
        restated_fee=recalc.fee_amount,
        fee_delta=quantize_cash(recalc.fee_amount - original_fee, rounding),
    )
