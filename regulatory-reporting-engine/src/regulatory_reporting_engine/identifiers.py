"""Financial and regulatory identifier validators (ISO 17442 LEI, ISO 6166 ISIN, ISO 4217, ISO 3166)."""

from __future__ import annotations

import re

from regulatory_reporting_engine.errors import InvalidIdentifierError

_LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_ENTITY_ID_RE = re.compile(r"^[A-Z0-9_-]{3,64}$")

ISO_4217_CURRENCIES = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "CAD",
        "AUD",
        "HKD",
        "SGD",
        "SEK",
        "NOK",
        "DKK",
        "NZD",
        "CNY",
        "INR",
        "BRL",
        "MXN",
        "ZAR",
        "KRW",
    }
)

ISO_3166_COUNTRIES = frozenset(
    {
        "US",
        "GB",
        "DE",
        "FR",
        "IE",
        "LU",
        "NL",
        "CH",
        "KY",
        "BM",
        "SG",
        "HK",
        "JP",
        "CA",
        "AU",
        "XS",  # Euroclear/Clearstream international prefix for ISINs
    }
)


def is_valid_lei(value: str) -> bool:
    """Validate an ISO 17442 20-character LEI including ISO 7064 Mod 97-10 check digits."""
    if not isinstance(value, str) or not _LEI_RE.match(value):
        return False
    numeric = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in value)
    return int(numeric) % 97 == 1


def validate_lei(value: str, *, field_name: str = "lei") -> str:
    if not is_valid_lei(value):
        raise InvalidIdentifierError(
            f"Invalid ISO 17442 LEI for '{field_name}': {value!r}",
            details={"field": field_name, "value": value, "standard": "ISO-17442"},
        )
    return value


def is_valid_isin(value: str) -> bool:
    """Validate an ISO 6166 12-character ISIN including Luhn check digit."""
    if not isinstance(value, str) or not _ISIN_RE.match(value):
        return False
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in value)
    total = 0
    for idx, ch in enumerate(reversed(digits)):
        d = int(ch)
        if idx % 2 == 1:
            d *= 2
            if d > 9:
                d = (d // 10) + (d % 10)
        total += d
    return total % 10 == 0


def validate_isin(value: str, *, field_name: str = "instrument_isin") -> str:
    if not is_valid_isin(value):
        raise InvalidIdentifierError(
            f"Invalid ISO 6166 ISIN for '{field_name}': {value!r}",
            details={"field": field_name, "value": value, "standard": "ISO-6166"},
        )
    return value


def validate_iso_currency(value: str, *, field_name: str = "currency") -> str:
    if not isinstance(value, str) or value not in ISO_4217_CURRENCIES:
        raise InvalidIdentifierError(
            f"Invalid or unsupported ISO 4217 currency code for '{field_name}': {value!r}",
            details={"field": field_name, "value": value, "standard": "ISO-4217"},
        )
    return value


def validate_iso_country(value: str, *, field_name: str = "country") -> str:
    if not isinstance(value, str) or value not in ISO_3166_COUNTRIES:
        raise InvalidIdentifierError(
            f"Invalid or unsupported ISO 3166 country code for '{field_name}': {value!r}",
            details={"field": field_name, "value": value, "standard": "ISO-3166"},
        )
    return value


def validate_entity_id(value: str, *, field_name: str = "entity_id") -> str:
    if not isinstance(value, str) or not _ENTITY_ID_RE.match(value):
        raise InvalidIdentifierError(
            f"Invalid canonical identifier for '{field_name}': {value!r}",
            details={"field": field_name, "value": value},
        )
    return value
