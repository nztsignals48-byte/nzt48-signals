"""ISA tax year — resets on 6 April UK."""
from __future__ import annotations

from datetime import date


def current_tax_year(today: date | None = None) -> int:
    d = today or date.today()
    return d.year if d >= date(d.year, 4, 6) else d.year - 1


def has_reset(prev_year: int, today: date | None = None) -> bool:
    return current_tax_year(today) > prev_year
