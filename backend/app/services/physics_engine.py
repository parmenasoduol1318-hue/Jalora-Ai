"""Domain physics engine (Phase 3) for Jalora.

Complements :mod:`app.services.math_engine` (SymPy) and
:mod:`app.services.math_numeric` (SciPy) with **dimensional** physics
computation on top of ``pint`` plus a physical-constants table
(``data/physics_constants.json``).

Design (mirrors the math engine's rules):
  * **compute-first, offline** — results are produced here; nothing is guessed;
  * **honest failure** — text that is not a recognised physics problem returns
    ``None`` so the caller can fall back to the model/other engines; we never
    invent a number;
  * **unit-safe** — ``pint`` performs the dimensional analysis, so an invalid
    unit combination is rejected instead of yielding a wrong number. If
    ``pint`` is unavailable, :func:`is_available` is ``False`` and
    :func:`solve` returns ``None``.

Public API:
  * :func:`is_available` — True when a units backend (pint) is present;
  * :func:`all_constants` / :func:`constant` — the physical-constants table;
  * :func:`lookup_constant` — resolve a constant from a natural-language query;
  * :func:`solve` — recognise and compute a physics problem, returning a
    :class:`~app.services.math_engine.MathResult` (or ``None``).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from app.config import BASE_DIR, get_settings
from app.services.math_engine import MathResult

logger = logging.getLogger("jalora.physics")

try:  # pragma: no cover - environment guard
    import pint

    _UREG = pint.UnitRegistry()
    _HAS_PINT = True
except Exception:  # noqa: BLE001
    _UREG = None  # type: ignore[assignment]
    _HAS_PINT = False


def is_available() -> bool:
    return _HAS_PINT


# ---------------------------------------------------------------------------
# physical constants
# ---------------------------------------------------------------------------

@dataclass
class Constant:
    name: str
    symbol: str
    value: float
    unit: str = ""
    exact: bool = False
    aliases: list[str] = field(default_factory=list)


_CONSTANTS: list[Constant] = []
_CONST_BY_SYMBOL: dict[str, Constant] = {}
_CONST_LOADED = False


def _load_constants() -> None:
    """Load ``data/physics_constants.json`` once (tolerant of a missing file)."""
    global _CONST_LOADED
    if _CONST_LOADED:
        return
    _CONST_LOADED = True
    candidates = [get_settings().data_dir / "physics_constants.json",
                  BASE_DIR / "data" / "physics_constants.json"]
    for path in candidates:
        try:
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                break
        except Exception:  # noqa: BLE001
            logger.exception("failed reading physics constants at %s", path)
    else:
        logger.warning("physics_constants.json not found (looked in %s)", candidates)
        return
    for item in raw.get("constants", []):
        try:
            c = Constant(
                name=str(item["name"]), symbol=str(item["symbol"]),
                value=float(item["value"]), unit=str(item.get("unit", "")),
                exact=bool(item.get("exact", False)),
                aliases=[str(a) for a in item.get("aliases", [])],
            )
        except (KeyError, TypeError, ValueError):
            continue
        _CONSTANTS.append(c)
        _CONST_BY_SYMBOL[c.symbol] = c


def all_constants() -> list[Constant]:
    _load_constants()
    return list(_CONSTANTS)


def constant(query: str) -> Constant | None:
    """Return the physical constant whose name/symbol/alias matches ``query``."""
    return _find_constant(query or "")


def _const_str(value: float) -> str:
    f = float(value)
    if f.is_integer() and abs(f) < 1e16:
        return str(int(f))
    return repr(f)


# Single-word aliases that are also ordinary English words: only accept them in
# an explicit query frame ("what is gravity", "the value of gravity") so prose
# such as "the gravity of the situation" is never read as a constant.
_WEAK_ALIASES = {
    "gravity", "planck", "faraday", "avogadro", "boltzmann",
    "permittivity", "permeability", "rydberg", "dalton", "electronvolt",
}
_QUERY_FRAME = re.compile(
    r"\b(constant|value|law|number|mass|charge|radius|speed|energy|equals?)\b"
    r"|\b(what is|what's|value of)\b"
)


def _find_constant(text: str) -> Constant | None:
    _load_constants()
    low = text.lower().strip()
    if not low:
        return None
    best: Constant | None = None
    best_len = 0
    weak_ok = bool(_QUERY_FRAME.search(low))
    for c in _CONSTANTS:
        for phrase in [c.name.lower(), *[a.lower() for a in c.aliases]]:
            if phrase and re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", low):
                if phrase in _WEAK_ALIASES and not weak_ok:
                    continue
                if len(phrase) > best_len:
                    best, best_len = c, len(phrase)
    if best is not None:
        return best
    # Symbol-only forms: require an explicit "value of X" / "what is X" frame so
    # a bare "c" or "G" in prose cannot be mistaken for a constant.
    for c in _CONSTANTS:
        sym = c.symbol.lower()
        if len(sym) >= 2 and re.search(rf"(?<![a-z0-9]){re.escape(sym)}(?![a-z0-9])", low):
            return c
        if len(sym) == 1 and re.search(
            rf"\b(value of|what is|what's)\s+(?:the\s+)?{re.escape(sym)}\b", low
        ):
            return c
    return None


def lookup_constant(text: str) -> MathResult | None:
    """Resolve a physical constant from a query, as a verified :class:`MathResult`."""
    c = _find_constant(text or "")
    if c is None:
        return None
    value = _const_str(c.value)
    unit = c.unit or ""
    steps = [f"Physical constant: {c.name} (symbol \\( {c.symbol} \\))."]
    shown = f"{value}\\;{unit}" if unit else value
    steps.append(f"Value: \\({shown}\\)" + ("  (exact, by SI definition)" if c.exact else ""))
    latex = f"{c.symbol} = {value}" + (f"\\;{unit}" if unit else "")
    return MathResult(
        ok=True, intent="physics", steps=steps, latex=latex,
        exact=value, numeric=value, units=unit, verified=True,
    )


# ---------------------------------------------------------------------------
# quantity extraction (number + unit)
# ---------------------------------------------------------------------------

_NUM = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"

# (literal token, canonical pint unit). Matched case-insensitively; longer
# tokens win so "m/s^2" beats "m" and "km" beats "m".
_UNIT_TOKENS: list[tuple[str, str]] = [
    ("kg/m^3", "kg/m**3"), ("kg/m3", "kg/m**3"), ("kg/m\u00b3", "kg/m**3"),
    ("g/cm^3", "g/cm**3"), ("g/cm3", "g/cm**3"), ("g/cm\u00b3", "g/cm**3"),
    ("m/s^2", "m/s**2"), ("m/s2", "m/s**2"), ("m/s\u00b2", "m/s**2"),
    ("meters per second squared", "m/s**2"), ("metres per second squared", "m/s**2"),
    ("m/s", "m/s"), ("meters per second", "m/s"), ("metres per second", "m/s"),
    ("km/h", "km/h"), ("km/hr", "km/h"), ("kmph", "km/h"), ("kph", "km/h"),
    ("kilometers per hour", "km/h"), ("kilometres per hour", "km/h"),
    ("mph", "mile/hour"), ("miles per hour", "mile/hour"),
    ("square meters", "m**2"), ("square metres", "m**2"), ("square meter", "m**2"),
    ("square metre", "m**2"), ("sq m", "m**2"), ("m^2", "m**2"), ("m2", "m**2"), ("m\u00b2", "m**2"),
    ("cm^2", "cm**2"), ("cm2", "cm**2"), ("cm\u00b2", "cm**2"),
    ("mm^2", "mm**2"), ("mm2", "mm**2"),
    ("cubic meters", "m**3"), ("cubic metres", "m**3"), ("cubic meter", "m**3"),
    ("cubic metre", "m**3"), ("m^3", "m**3"), ("m3", "m**3"), ("m\u00b3", "m**3"),
    ("cm^3", "cm**3"), ("cm3", "cm**3"), ("cm\u00b3", "cm**3"), ("cc", "cm**3"),
    ("milliliter", "mL"), ("milliliters", "mL"), ("millilitre", "mL"), ("millilitres", "mL"),
    ("mL", "mL"), ("ml", "mL"),
    ("kilojoules", "kJ"), ("kilojoule", "kJ"), ("kJ", "kJ"),
    ("kilowatts", "kW"), ("kilowatt", "kW"), ("kW", "kW"),
    ("kilopascals", "kPa"), ("kilopascal", "kPa"), ("kPa", "kPa"),
    ("kilometers", "km"), ("kilometer", "km"), ("kilometres", "km"), ("kilometre", "km"), ("km", "km"),
    ("centimeters", "cm"), ("centimeter", "cm"), ("centimetres", "cm"), ("centimetre", "cm"), ("cm", "cm"),
    ("millimeters", "mm"), ("millimeter", "mm"), ("millimetres", "mm"), ("millimetre", "mm"), ("mm", "mm"),
    ("nanometers", "nm"), ("nanometer", "nm"), ("nanometres", "nm"), ("nanometre", "nm"), ("nm", "nm"),
    ("kilogrammes", "kg"), ("kilogramme", "kg"), ("kilograms", "kg"), ("kilogram", "kg"), ("kg", "kg"),
    ("milligrams", "mg"), ("milligram", "mg"), ("mg", "mg"),
    ("grams", "g"), ("gram", "g"), ("g", "g"),
    ("pounds", "lb"), ("pound", "lb"), ("lbs", "lb"), ("lb", "lb"),
    ("tonnes", "tonne"), ("tonne", "tonne"),
    ("meters", "m"), ("meter", "m"), ("metres", "m"), ("metre", "m"), ("m", "m"),
    ("feet", "foot"), ("foot", "foot"), ("ft", "foot"),
    ("inches", "inch"), ("inch", "inch"),
    ("miles", "mile"), ("mile", "mile"),
    ("milliseconds", "ms"), ("millisecond", "ms"), ("ms", "ms"),
    ("microseconds", "us"), ("microsecond", "us"),
    ("minutes", "min"), ("minute", "min"), ("mins", "min"), ("min", "min"),
    ("hours", "hour"), ("hour", "hour"), ("hrs", "hour"), ("hr", "hour"), ("h", "hour"),
    ("seconds", "s"), ("second", "s"), ("secs", "s"), ("sec", "s"), ("s", "s"),
    ("newtons", "N"), ("newton", "N"), ("N", "N"),
    ("joules", "J"), ("joule", "J"), ("J", "J"),
    ("watts", "W"), ("watt", "W"), ("W", "W"),
    ("pascals", "Pa"), ("pascal", "Pa"), ("Pa", "Pa"),
    ("bar", "bar"), ("atm", "atm"),
    ("coulombs", "C"), ("coulomb", "C"), ("C", "C"),
    ("volts", "V"), ("volt", "V"), ("V", "V"),
    ("amperes", "A"), ("ampere", "A"), ("amps", "A"), ("amp", "A"), ("A", "A"),
    ("ohms", "ohm"), ("ohm", "ohm"), ("\u03a9", "ohm"), ("\u03c9", "ohm"),
    ("hertz", "Hz"), ("Hz", "Hz"), ("kHz", "kHz"), ("MHz", "MHz"),
    ("electronvolts", "eV"), ("electronvolt", "eV"), ("eV", "eV"),
    ("degrees kelvin", "K"), ("kelvin", "K"), ("K", "K"),
    ("degrees celsius", "degC"), ("celsius", "degC"), ("centigrade", "degC"),
    ("degC", "degC"), ("degc", "degC"), ("\u00b0C", "degC"), ("\u00b0c", "degC"),
    ("degrees fahrenheit", "degF"), ("fahrenheit", "degF"),
    ("degF", "degF"), ("degf", "degF"), ("\u00b0F", "degF"), ("\u00b0f", "degF"),
    ("moles", "mol"), ("mole", "mol"), ("mol", "mol"),
    ("liters", "L"), ("liter", "L"), ("litres", "L"), ("litre", "L"), ("L", "L"), ("l", "L"),
]

_UNIT_MAP: dict[str, str] = {}
for _lit, _canon in _UNIT_TOKENS:
    _UNIT_MAP[_lit.lower()] = _canon

_ALT = "|".join(re.escape(lit) for lit, _ in sorted(_UNIT_TOKENS, key=lambda x: len(x[0]), reverse=True))
_QTY_RE = re.compile(rf"(?<![A-Za-z0-9.])({_NUM})\s*({_ALT})(?![A-Za-z0-9])", re.IGNORECASE)


def _extract_quantities(text: str) -> list[tuple[float, str]]:
    """Return ``[(value, canonical_unit), ...]`` found in ``text``."""
    out: list[tuple[float, str]] = []
    for m in _QTY_RE.finditer(text):
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        unit = _UNIT_MAP.get(m.group(2).lower())
        if unit is None:
            continue
        out.append((value, unit))
    return out


def _dim_ok(unit: str, dim: str) -> bool:
    try:
        return bool(_UREG.Quantity(1, unit).check(dim))
    except Exception:  # noqa: BLE001
        return False


def _convert(value: float, unit: str, target: str) -> float:
    return float(_UREG.Quantity(value, unit).to(target).magnitude)


# ---------------------------------------------------------------------------
# formula registry
# ---------------------------------------------------------------------------

@dataclass
class Var:
    sym: str
    dim: str
    unit: str


@dataclass
class Formula:
    name: str
    kw: list[str]
    vars: list[Var]
    result_unit: str
    result_display: str
    latex: str
    fn: Callable[[dict], float]


@dataclass
class Solvable:
    """A formula where exactly one of ``vars`` may be missing and is computed."""
    name: str
    kw: list[str]
    vars: list[Var]
    produce: dict[str, tuple[str, str, Callable[[dict], float]]]  # sym -> (unit, display, fn)
    latex: str


def _cval(symbol: str) -> float:
    c = _CONST_BY_SYMBOL.get(symbol)
    if c is None:
        raise KeyError(symbol)
    return float(c.value)


_FORMULAS: list[Formula] = [
    Formula(
        "Newton's law of universal gravitation",
        ["gravitational force", "gravitational attraction", "gravity between",
         "law of gravitation", "newton's law of gravitation"],
        [Var("m1", "[mass]", "kg"), Var("m2", "[mass]", "kg"), Var("r", "[length]", "m")],
        "N", "N", r"F = G\,\frac{m_1 m_2}{r^{2}}",
        lambda q: _cval("G") * q["m1"] * q["m2"] / q["r"] ** 2,
    ),
    Formula(
        "Coulomb's law",
        ["coulomb", "electrostatic force", "electric force", "coulomb's law"],
        [Var("q1", "[charge]", "C"), Var("q2", "[charge]", "C"), Var("r", "[length]", "m")],
        "N", "N", r"F = k_e\,\frac{q_1 q_2}{r^{2}}",
        lambda q: _cval("k_e") * q["q1"] * q["q2"] / q["r"] ** 2,
    ),
    Formula(
        "kinetic energy",
        ["kinetic energy"],
        [Var("m", "[mass]", "kg"), Var("v", "[velocity]", "m/s")],
        "J", "J", r"E_k = \tfrac{1}{2} m v^{2}",
        lambda q: 0.5 * q["m"] * q["v"] ** 2,
    ),
    Formula(
        "gravitational potential energy",
        ["potential energy", "gravitational potential energy", "potential energy of"],
        [Var("m", "[mass]", "kg"), Var("h", "[length]", "m")],
        "J", "J", r"E_p = m g h",
        lambda q: q["m"] * _cval("g_0") * q["h"],
    ),
    Formula(
        "weight",
        ["weight", "weighs", "force of gravity", "force due to gravity", "gravitational force on"],
        [Var("m", "[mass]", "kg")],
        "N", "N", r"W = m g",
        lambda q: q["m"] * _cval("g_0"),
    ),
    Formula(
        "work done",
        ["work done", "work"],
        [Var("F", "[force]", "N"), Var("d", "[length]", "m")],
        "J", "J", r"W = F d",
        lambda q: q["F"] * q["d"],
    ),
    Formula(
        "momentum",
        ["momentum"],
        [Var("m", "[mass]", "kg"), Var("v", "[velocity]", "m/s")],
        "kg*m/s", "kg\u00b7m/s", r"p = m v",
        lambda q: q["m"] * q["v"],
    ),
    Formula(
        "density",
        ["density"],
        [Var("m", "[mass]", "kg"), Var("V", "[volume]", "m**3")],
        "kg/m**3", "kg/m\u00b3", r"\rho = \frac{m}{V}",
        lambda q: q["m"] / q["V"],
    ),
    Formula(
        "pressure",
        ["pressure"],
        [Var("F", "[force]", "N"), Var("A", "[area]", "m**2")],
        "Pa", "Pa", r"P = \frac{F}{A}",
        lambda q: q["F"] / q["A"],
    ),
    Formula(
        "electric power",
        ["electrical power", "electric power", "power dissipated"],
        [Var("V", "[electric_potential]", "V"), Var("I", "[current]", "A")],
        "W", "W", r"P = V I",
        lambda q: q["V"] * q["I"],
    ),
    Formula(
        "mechanical power",
        ["power"],
        [Var("W", "[energy]", "J"), Var("t", "[time]", "s")],
        "W", "W", r"P = \frac{W}{t}",
        lambda q: q["W"] / q["t"],
    ),
    Formula(
        "Newton's second law",
        ["force", "net force"],
        [Var("m", "[mass]", "kg"), Var("a", "[acceleration]", "m/s**2")],
        "N", "N", r"F = m a",
        lambda q: q["m"] * q["a"],
    ),
    Formula(
        "acceleration",
        ["acceleration", "accelerates", "speeds up", "speeding up", "decelerates"],
        [Var("u", "[velocity]", "m/s"), Var("v", "[velocity]", "m/s"), Var("t", "[time]", "s")],
        "m/s**2", "m/s\u00b2", r"a = \frac{v - u}{t}",
        lambda q: (q["v"] - q["u"]) / q["t"],
    ),
    Formula(
        "wavelength",
        ["wavelength"],
        [Var("v", "[velocity]", "m/s"), Var("f", "[frequency]", "Hz")],
        "m", "m", r"\lambda = \frac{v}{f}",
        lambda q: q["v"] / q["f"],
    ),
    Formula(
        "wave frequency",
        ["frequency", "wave frequency"],
        [Var("v", "[velocity]", "m/s"), Var("lam", "[length]", "m")],
        "Hz", "Hz", r"f = \frac{v}{\lambda}",
        lambda q: q["v"] / q["lam"],
    ),
]

_SOLVABLES: list[Solvable] = [
    Solvable(
        "Ohm's law",
        ["ohm", "ohms law", "ohm's law", "resistance", "voltage", "current"],
        [Var("V", "[electric_potential]", "V"), Var("I", "[current]", "A"), Var("R", "[resistance]", "ohm")],
        {
            "V": ("V", "V", lambda q: q["I"] * q["R"]),
            "I": ("A", "A", lambda q: q["V"] / q["R"]),
            "R": ("ohm", "\u03a9", lambda q: q["V"] / q["I"]),
        },
        r"V = I R",
    ),
    Solvable(
        "ideal gas law",
        ["ideal gas", "pv = nrt", "pv=nrt"],
        [Var("P", "[pressure]", "Pa"), Var("V", "[volume]", "m**3"),
         Var("n", "[substance]", "mol"), Var("T", "[temperature]", "K")],
        {
            "P": ("Pa", "Pa", lambda q: q["n"] * _cval("R") * q["T"] / q["V"]),
            "V": ("m**3", "m\u00b3", lambda q: q["n"] * _cval("R") * q["T"] / q["P"]),
            "n": ("mol", "mol", lambda q: q["P"] * q["V"] / (_cval("R") * q["T"])),
            "T": ("K", "K", lambda q: q["P"] * q["V"] / (q["n"] * _cval("R"))),
        },
        r"P V = n R T",
    ),
]


def _kw_hit(kw: list[str], low: str) -> bool:
    return any(re.search(rf"\b{re.escape(k)}\b", low) for k in kw)


def _assign(vars_: list[Var], qs: list[tuple[float, str]]) -> dict[str, float] | None:
    """Assign each variable a quantity of matching dimension (None if not all)."""
    out = _assign_partial(vars_, qs)
    if out is not None and len(out) == len(vars_):
        return out
    return None


def _fmt(value: float) -> str:
    # ".6g" already renders 0.0 as "0"; near-integers snap to ints too.
    return f"{float(value):.6g}"


def _formula_result(f: Formula, value: float) -> MathResult:
    disp = f.result_display
    steps = [
        f"Identify the quantity: {f.name}.",
        f"Apply the formula \\( {f.latex} \\).",
        "Substitute the given values and evaluate (all inputs converted to SI units).",
    ]
    return MathResult(
        ok=True, intent="physics", steps=steps, latex=f.latex,
        exact=_fmt(value), numeric=_fmt(value), units=disp, verified=True,
    )


def _solvable_result(f: Solvable, missing: Var, value: float) -> MathResult:
    unit, disp, _fn = f.produce[missing.sym]
    steps = [
        f"Identify the quantity to find: {f.name} (solve for \\( {missing.sym} \\)).",
        f"Apply the relation \\( {f.latex} \\).",
        "Rearrange for the unknown, substitute the given values and evaluate (SI units).",
    ]
    return MathResult(
        ok=True, intent="physics", steps=steps, latex=f.latex,
        exact=_fmt(value), numeric=_fmt(value), units=disp, verified=True,
    )


# ---------------------------------------------------------------------------
# public solver
# ---------------------------------------------------------------------------

def solve(text: str) -> MathResult | None:
    """Recognise and compute a physics problem, else ``None``.

    Order: dimensional formula solvers, then solve-for-missing relations, then
    a physical-constant lookup. Anything unrecognised returns ``None`` so the
    caller can fall back (never fabricate).
    """
    if not _HAS_PINT or not text or not text.strip():
        return None
    _load_constants()
    low = text.lower()
    qs = _extract_quantities(text)

    if qs:
        for f in _FORMULAS:
            if not _kw_hit(f.kw, low):
                continue
            assigned = _assign(f.vars, qs)
            if assigned is None:
                continue
            try:
                value = float(f.fn(assigned))
            except Exception:  # noqa: BLE001
                continue
            return _formula_result(f, value)

        for f in _SOLVABLES:
            if not _kw_hit(f.kw, low):
                continue
            assigned = _assign(f.vars, qs) if len(qs) >= len(f.vars) else None
            if assigned is None:
                # allow exactly one missing variable
                partial = _assign_partial(f.vars, qs)
                if partial is None or len(partial) != len(f.vars) - 1:
                    continue
                missing = [v for v in f.vars if v.sym not in partial]
                if len(missing) != 1:
                    continue
                mv = missing[0]
                _unit, _disp, fn = f.produce[mv.sym]
                try:
                    value = float(fn(partial))
                except Exception:  # noqa: BLE001
                    continue
                return _solvable_result(f, mv, value)

    return lookup_constant(text)


def _assign_partial(vars_: list[Var], qs: list[tuple[float, str]]) -> dict[str, float] | None:
    remaining = list(qs)
    out: dict[str, float] = {}
    for var in vars_:
        for i, (value, unit) in enumerate(remaining):
            if _dim_ok(unit, var.dim):
                try:
                    out[var.sym] = _convert(value, unit, var.unit)
                except Exception:  # noqa: BLE001
                    continue
                del remaining[i]
                break
    return out or None
