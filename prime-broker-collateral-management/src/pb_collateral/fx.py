"""Exact FX rate table and deterministic currency conversion."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, Optional, Tuple

from pb_collateral.errors import MissingConfigurationError, ValidationError
from pb_collateral.models import FxRate, SUPPORTED_CURRENCIES, quantize_money


class FxTable:
    """Immutable FX rate lookup table supporting direct, inverse, and USD triangulation."""

    def __init__(
        self,
        rates: Iterable[FxRate],
        rounding_mode: str = "ROUND_HALF_EVEN",
    ) -> None:
        self._rates: Dict[Tuple[str, str], FxRate] = {}
        self._rounding_mode = rounding_mode
        for r in rates:
            if r.base_currency not in SUPPORTED_CURRENCIES:
                raise ValidationError(
                    f"Unsupported FX base currency: {r.base_currency}",
                    context={"currency": r.base_currency},
                )
            if r.quote_currency not in SUPPORTED_CURRENCIES:
                raise ValidationError(
                    f"Unsupported FX quote currency: {r.quote_currency}",
                    context={"currency": r.quote_currency},
                )
            if r.rate <= Decimal("0"):
                raise ValidationError(
                    f"FX rate must be positive: {r.base_currency}/{r.quote_currency}={r.rate}",
                    context={"pair": f"{r.base_currency}/{r.quote_currency}", "rate": str(r.rate)},
                )
            self._rates[(r.base_currency, r.quote_currency)] = r

    def get_rate(self, from_currency: str, to_currency: str, as_of_date: Optional[date] = None) -> Decimal:
        """Return exact conversion multiplier: 1 unit of from_currency in to_currency."""
        if from_currency == to_currency:
            return Decimal("1")

        direct = self._rates.get((from_currency, to_currency))
        if direct is not None:
            return direct.rate

        inverse = self._rates.get((to_currency, from_currency))
        if inverse is not None:
            return Decimal("1") / inverse.rate

        # Triangulate through USD if both legs exist
        if from_currency != "USD" and to_currency != "USD":
            leg1 = self._try_direct_or_inverse(from_currency, "USD")
            leg2 = self._try_direct_or_inverse("USD", to_currency)
            if leg1 is not None and leg2 is not None:
                return leg1 * leg2

        raise MissingConfigurationError(
            f"Missing FX rate for {from_currency}/{to_currency}",
            context={
                "from_currency": from_currency,
                "to_currency": to_currency,
                "as_of_date": as_of_date.isoformat() if as_of_date else None,
            },
        )

    def _try_direct_or_inverse(self, base: str, quote: str) -> Optional[Decimal]:
        direct = self._rates.get((base, quote))
        if direct is not None:
            return direct.rate
        inverse = self._rates.get((quote, base))
        if inverse is not None:
            return Decimal("1") / inverse.rate
        return None

    def convert(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        as_of_date: Optional[date] = None,
    ) -> Tuple[Decimal, Decimal]:
        """Convert amount to target currency, returning (converted_quantized, fx_rate_used)."""
        rate = self.get_rate(from_currency, to_currency, as_of_date=as_of_date)
        converted = quantize_money(amount * rate, to_currency, rounding_mode=self._rounding_mode)
        return converted, rate
