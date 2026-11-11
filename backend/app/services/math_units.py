"""Real unit algebra for Jalora (Phase 1) — powered by ``pint``.

Complements :mod:`app.services.math_engine`'s small manual unit table with full
dimensional analysis: compound units (``km/h``, ``m/s^2``, ``kg*m/s^2``), SI
derived units (``N``, ``J``, ``W``, ``Pa``), non-integer powers and affine
temperature scales, plus a clear "incompatible dimensions" error instead of a
wrong number.

The module is import-light and degrades gracefully: if ``pint`` is unavailable,
:func:`is_available` is ``False`` and callers fall back to the manual table.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

logger = logging.getLogger("jalora.units")

try:  # pragma: no cover - environment guard
    import pint

    _UREG = pint.UnitRegistry()
    _HAS_PINT = True
except Exception:  # noqa: BLE001
    _UREG = None  # type: ignore[assignment]
    _HAS_PINT = False


# Friendly unit words -> pint identifiers (pint's own aliases cover the rest).
_UNIT_ALIASES: dict[str, str] = {
    "celsius": "degC", "\u00b0c": "degC", "degc": "degC", "centigrade": "degC",
    "fahrenheit": "degF", "\u00b0f": "degF", "degf": "degF",
    "kelvin": "K", "\u00b0k": "K",
    "kph": "km/hour", "kmph": "km/hour", "kmh": "km/hour",
    "meters": "meter", "metres": "meter", "metre": "meter",
    "grams": "gram", "kilograms": "kilogram", "pounds": "pound",
    "inches": "inch", "feet": "foot", "yards": "yard", "miles": "mile",
    "seconds": "second", "minutes": "minute", "hours": "hour",
    "liters": "liter", "litres": "liter",
    "newtons": "newton", "joules": "joule", "watts": "watt", "pascals": "pascal",
    "volts": "volt", "amps": "ampere", "amperes": "ampere", "ohms": "ohm",
    "moles": "mole",
}


@dataclass
class UnitOutcome:
    """Outcome of a unit conversion (mirrors the fields math_engine needs)."""
    ok: bool
    exact: str = ""
    numeric: str = ""
    units: str = ""
    latex: str = ""
    steps: list[str] = field(default_factory=list)
    error: str | None = None


def is_available() -> bool:
    return _HAS_PINT


def _fmt(value: float) -> str:
    """Compact decimal: near-integer floats snap to integers, else trimmed."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(v - round(v)) < 1e-9 * max(1.0, abs(v)):
        return str(int(round(v)))
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return str(v)
    return format(d, "f").rstrip("0").rstrip(".")[:16]


def _fmt_unit(unit: "pint.Unit") -> str:
    """Short unit symbol with spacing around ``/`` and ``*`` collapsed."""
    return re.sub(r"\s*([/*])\s*", r"\1", f"{unit:~}")


def _norm_unit(raw: str) -> str:
    u = raw.strip().replace("**", "^")
    u = re.sub(r"\bper\b", " / ", u, flags=re.IGNORECASE)
    # unit words are case-sensitive in pint (m vs M); only alias known words.
    key = u.lower().replace(" ", "")
    return _UNIT_ALIASES.get(key, u)


def _parse(text: str):
    """Extract (value, src_unit, dst_unit) from a conversion phrase, else None."""
    t = re.sub(r"^\s*(convert|change|turn|express)\s+", "", text, flags=re.IGNORECASE).strip()
    # "how many <dst> in <num> <src>"  /  "<dst> in <num> <src>"
    m = re.match(
        r"^(?:how\s+many\s+)?([a-zA-Z\u00b0][\w\u00b0/*^\-\. ]*?)\s+in\s+"
        r"([\d.]+(?:[eE][+-]?\d+)?)\s*([a-zA-Z\u00b0][\w\u00b0/*^\-\. ]*?)\s*$",
        t, re.IGNORECASE,
    )
    if m:
        return float(m.group(2)), m.group(3).strip(), m.group(1).strip()
    # "<num> <src> (to|in|into|->|=) <dst>"
    m = re.match(
        r"^([\d.]+(?:[eE][+-]?\d+)?)\s*([a-zA-Z\u00b0][\w\u00b0/*^\-\. ]*?)\s+"
        r"(?:to|in|into|->|=)\s+([a-zA-Z\u00b0][\w\u00b0/*^\-\. ]*?)\s*$",
        t, re.IGNORECASE,
    )
    if m:
        return float(m.group(1)), m.group(2).strip(), m.group(3).strip()
    return None


def convert(text: str) -> UnitOutcome | None:
    """Convert a single quantity between units. Returns None when ``text`` is not
    a recognisable conversion (caller falls back to the manual table)."""
    if not _HAS_PINT:
        return None
    parsed = _parse(text)
    if parsed is None:
        return None
    value, src_raw, dst_raw = parsed
    src, dst = _norm_unit(src_raw), _norm_unit(dst_raw)
    try:
        src_u = _UREG.Unit(src)
    except Exception:  # noqa: BLE001
        return None  # unknown source unit -> not a conversion we understand
    try:
        dst_u = _UREG.Unit(dst)
    except Exception:  # noqa: BLE001
        return None  # unknown target unit -> not a conversion we understand
    if not src_u.dimensionality == dst_u.dimensionality:
        return UnitOutcome(
            ok=False,
            error=f"Cannot convert {src_raw} to {dst_raw} (incompatible dimensions).",
        )
    try:
        q = _UREG.Quantity(value, src_u).to(dst_u)
    except Exception as exc:  # noqa: BLE001
        return UnitOutcome(ok=False, error=f"Conversion failed: {exc}")
    mag = float(q.magnitude)
    exact = _fmt(mag)
    unit_str = _fmt_unit(dst_u)
    return UnitOutcome(
        ok=True,
        exact=exact,
        numeric=exact,
        units=unit_str,
        latex=f"{exact}\\,{unit_str}",
        steps=[
            f"Convert {_fmt(value)} {src_raw} to {dst_raw}.",
            f"Use the defined conversion from {src_raw} to {dst_raw}.",
            f"\\({exact}\\,{unit_str}\\)",
        ],
    )
