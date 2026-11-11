"""Domain chemistry engine (Phase 4) for Jalora.

Complements the math/physics engines with offline chemistry computation backed
by a periodic-table table (``data/periodic_table.json``):

* **element lookup** — by symbol, name (incl. common aliases) or atomic number;
* **molar mass** — parse a chemical formula (nested groups, hydrates, unicode
  subscripts) and sum the standard atomic weights;
* **equation balancing** — integer-nullspace solve via SymPy;
* **mole/mass conversions** — mass <-> moles <-> number of particles.

Design (mirrors :mod:`app.services.physics_engine`):
  * **compute-first, offline** — results are produced here; nothing is guessed;
  * **honest failure** — unrecognised text returns ``None`` so the caller can
    fall back; we never invent a formula, a mass or a balanced equation;
  * pure-Python + JSON, so it works whether or not ``periodictable``/``chempy``
    are installed (they are not required; SymPy, already present, is used only
    for balancing and degrades gracefully when absent).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.config import BASE_DIR, get_settings
from app.services.math_engine import MathResult

logger = logging.getLogger("jalora.chemistry")

try:  # pragma: no cover - environment guard
    import sympy as _sp

    _HAS_SYMPY = True
except Exception:  # noqa: BLE001
    _sp = None  # type: ignore[assignment]
    _HAS_SYMPY = False

# Avogadro constant (SI-exact) — mirrors data/physics_constants.json.
_AVOGADRO = 6.02214076e23


def is_available() -> bool:
    return True


# ---------------------------------------------------------------------------
# periodic table
# ---------------------------------------------------------------------------

@dataclass
class Element:
    z: int
    symbol: str
    name: str
    mass: float
    category: str = ""
    group: int | None = None
    period: int | None = None


_ELEMENTS: list[Element] = []
_BY_SYMBOL: dict[str, Element] = {}
_BY_NAME: dict[str, Element] = {}
_BY_Z: dict[int, Element] = {}
_LOADED = False

# Common name variants -> canonical element name (lower-cased keys).
_NAME_ALIASES: dict[str, str] = {
    "aluminum": "aluminium", "cesium": "caesium", "sulphur": "sulfur",
    "wolfram": "tungsten", "quicksilver": "mercury",
}

# Common compound names -> formula (lower-cased keys).
_COMPOUND_NAMES: dict[str, str] = {
    "water": "H2O", "glucose": "C6H12O6", "methane": "CH4",
    "carbon dioxide": "CO2", "carbon monoxide": "CO", "ammonia": "NH3",
    "ethanol": "C2H5OH", "methanol": "CH3OH", "table salt": "NaCl",
    "sodium chloride": "NaCl", "sulfuric acid": "H2SO4", "sulphuric acid": "H2SO4",
    "hydrochloric acid": "HCl", "nitric acid": "HNO3", "acetic acid": "CH3COOH",
    "sodium hydroxide": "NaOH", "hydrogen peroxide": "H2O2",
    "calcium carbonate": "CaCO3", "sodium bicarbonate": "NaHCO3",
    "baking soda": "NaHCO3", "rust": "Fe2O3", "ozone": "O3",
    "hydrogen": "H2", "oxygen": "O2", "nitrogen": "N2", "chlorine": "Cl2",
}


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    candidates = [get_settings().data_dir / "periodic_table.json",
                  BASE_DIR / "data" / "periodic_table.json"]
    for path in candidates:
        try:
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                break
        except Exception:  # noqa: BLE001
            logger.exception("failed reading periodic table at %s", path)
    else:
        logger.warning("periodic_table.json not found (looked in %s)", candidates)
        return
    for item in raw.get("elements", []):
        try:
            el = Element(
                z=int(item["z"]), symbol=str(item["symbol"]), name=str(item["name"]),
                mass=float(item["mass"]), category=str(item.get("category", "")),
                group=item.get("group"), period=item.get("period"),
            )
        except (KeyError, TypeError, ValueError):
            continue
        _ELEMENTS.append(el)
        _BY_SYMBOL[el.symbol] = el
        _BY_SYMBOL[el.symbol.lower()] = el
        _BY_NAME[el.name.lower()] = el
        _BY_Z[el.z] = el


def all_elements() -> list[Element]:
    _load()
    return list(_ELEMENTS)


def element(query: str) -> Element | None:
    """Look up an element by symbol, name (or alias), or atomic number."""
    _load()
    q = (query or "").strip().strip("?.!,; ").strip()
    if not q:
        return None
    if re.fullmatch(r"\d+", q):
        return _BY_Z.get(int(q))
    return _BY_SYMBOL.get(q.capitalize()) or _BY_SYMBOL.get(q) or _BY_NAME.get(
        _NAME_ALIASES.get(q.lower(), q.lower())
    )


# ---------------------------------------------------------------------------
# formula parsing
# ---------------------------------------------------------------------------

_SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _parse_group(s: str) -> dict[str, int] | None:
    """Parse a (possibly parenthesised) formula fragment into symbol->count."""
    counts: dict[str, int] = {}
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "([":
            depth, j = 1, i + 1
            while j < n and depth > 0:
                if s[j] in "([":
                    depth += 1
                elif s[j] in ")]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= n:
                return None
            sub = _parse_group(s[i + 1:j])
            if sub is None:
                return None
            k = j + 1
            num = ""
            while k < n and s[k].isdigit():
                num += s[k]
                k += 1
            mult = int(num) if num else 1
            for sym, cnt in sub.items():
                counts[sym] = counts.get(sym, 0) + cnt * mult
            i = k
        elif c.isupper():
            sym = c
            i += 1
            if i < n and s[i].islower():
                sym += s[i]
                i += 1
            num = ""
            while i < n and s[i].isdigit():
                num += s[i]
                i += 1
            if _BY_SYMBOL.get(sym) is None:
                return None
            counts[sym] = counts.get(sym, 0) + (int(num) if num else 1)
        else:
            return None
    return counts


def parse_formula(formula: str) -> dict[str, int] | None:
    """Parse a chemical formula into ``{symbol: count}`` (or ``None``).

    Handles nested groups (``Ca(OH)2``), hydrates (``CuSO4·5H2O``) and unicode
    subscripts (``H₂O``); a leading stoichometric coefficient is ignored.
    """
    _load()
    if not formula:
        return None
    s = formula.translate(_SUBSCRIPT).replace(" ", "")
    total: dict[str, int] = {}
    for part in re.split(r"[·.*]", s):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d*)(.*)$", part)
        if m is None:
            return None
        mult = int(m.group(1)) if m.group(1) else 1
        counts = _parse_group(m.group(2))
        if counts is None:
            return None
        for sym, cnt in counts.items():
            total[sym] = total.get(sym, 0) + cnt * mult
    return total or None


def molar_mass(formula: str) -> float | None:
    """Molar mass in g/mol of ``formula`` (``None`` if unknown)."""
    _load()
    counts = parse_formula(formula)
    if counts is None:
        return None
    total = 0.0
    for sym, cnt in counts.items():
        el = _BY_SYMBOL.get(sym)
        if el is None:
            return None
        total += el.mass * cnt
    return total or None


def _resolve_formula(text: str) -> str:
    """Clean a captured phrase into a formula, resolving common names."""
    cleaned = (text or "").strip().strip("?.!,; ").strip()
    cleaned = re.sub(r"\b(in|of)\s+grams?$", "", cleaned, flags=re.IGNORECASE).strip()
    key = cleaned.lower()
    return _COMPOUND_NAMES.get(key, cleaned)


# ---------------------------------------------------------------------------
# equation balancing
# ---------------------------------------------------------------------------

def _split_species(side: str) -> list[str]:
    return [p.strip() for p in re.split(r"[+]", side) if p.strip()]


def balance_equation(equation: str) -> tuple[list[str], list[str], list[int]] | None:
    """Balance a chemical equation. Returns ``(lhs, rhs, coeffs)`` or ``None``."""
    if not _HAS_SYMPY:
        return None
    assert _sp is not None
    parts = re.split(r"->|=>|→|⟶|=", equation)
    if len(parts) != 2:
        return None
    lhs = _split_species(parts[0])
    rhs = _split_species(parts[1])
    if not lhs or not rhs:
        return None
    species = lhs + rhs
    counts: list[dict[str, int]] = []
    for spec in species:
        c = parse_formula(spec)
        if c is None:
            return None
        counts.append(c)
    elements = sorted({e for c in counts for e in c})
    if not elements:
        return None
    matrix = [
        [(c.get(e, 0) if idx < len(lhs) else -c.get(e, 0)) for idx, c in enumerate(counts)]
        for e in elements
    ]
    try:
        null = _sp.Matrix(matrix).nullspace()
    except Exception:  # noqa: BLE001
        return None
    if len(null) != 1:
        return None
    vec = null[0]
    try:
        from sympy import ilcm
        factor = 1
        for x in vec:
            factor = int(ilcm(factor, _sp.denom(x)))
        ints = [int(x * factor) for x in vec]
        g = 0
        for v in ints:
            g = int(_sp.gcd(g, abs(v)))
        if g:
            ints = [v // g for v in ints]
        if all(v <= 0 for v in ints):
            ints = [-v for v in ints]
        if any(v <= 0 for v in ints):
            return None
    except Exception:  # noqa: BLE001
        return None
    return lhs, rhs, ints


def _format_equation(lhs: list[str], rhs: list[str], coeffs: list[int]) -> str:
    def side(species: list[str], cs: list[int]) -> str:
        terms = []
        for spec, c in zip(species, cs):
            terms.append(spec if c == 1 else f"{c} {spec}")
        return " + ".join(terms)
    return f"{side(lhs, coeffs[:len(lhs)])} -> {side(rhs, coeffs[len(lhs):])}"


# ---------------------------------------------------------------------------
# result helpers
# ---------------------------------------------------------------------------

def _fmt(value: float) -> str:
    return f"{float(value):.6g}"


def _element_number_result(el: Element) -> MathResult:
    return MathResult(
        ok=True, intent="chemistry", verified=True, exact=str(el.z),
        steps=[f"{el.name} ({el.symbol}) is element number {el.z}.",
               f"Category: {el.category}." + (f" Group {el.group}, period {el.period}."
                                              if el.group else f" Period {el.period}.")],
    )


def _element_mass_result(el: Element) -> MathResult:
    m = _fmt(el.mass)
    return MathResult(
        ok=True, intent="chemistry", verified=True, exact=m, numeric=m, units="u",
        steps=[f"Standard atomic weight of {el.name} ({el.symbol}): {m} u.",
               f"Molar mass of the element = {m} g/mol."],
    )


def _symbol_result(el: Element) -> MathResult:
    return MathResult(
        ok=True, intent="chemistry", verified=True, exact=el.symbol,
        steps=[f"The chemical symbol for {el.name} is {el.symbol}.",
               f"Atomic number: {el.z}."],
    )


def _name_result(el: Element) -> MathResult:
    return MathResult(
        ok=True, intent="chemistry", verified=True, exact=el.name,
        steps=[f"{el.symbol} is {el.name} (atomic number {el.z})."],
    )


def _molar_mass_result(formula: str, mass: float, counts: dict[str, int]) -> MathResult:
    parts = []
    for sym, cnt in counts.items():
        el = _BY_SYMBOL[sym]
        parts.append(f"{cnt}\u00d7{el.mass:g}")
    m = _fmt(mass)
    return MathResult(
        ok=True, intent="chemistry", verified=True, exact=m, numeric=m, units="g/mol",
        latex=rf"M({formula}) = {m}\ \mathrm{{g\,mol^{{-1}}}}",
        steps=[f"Molar mass of {formula}: sum of the atomic masses of its atoms.",
               " + ".join(parts) + f" = {m} g/mol."],
    )


def _moles_from_mass_result(mass_g: float, formula: str, mass: float, n: float) -> MathResult:
    return MathResult(
        ok=True, intent="chemistry", verified=True, exact=_fmt(n), numeric=_fmt(n), units="mol",
        steps=[f"Molar mass of {formula} = {_fmt(mass)} g/mol.",
               f"n = m / M = {_fmt(mass_g)} / {_fmt(mass)} = {_fmt(n)} mol."],
    )


def _mass_from_moles_result(mol: float, formula: str, mass: float, grams: float) -> MathResult:
    return MathResult(
        ok=True, intent="chemistry", verified=True, exact=_fmt(grams), numeric=_fmt(grams), units="g",
        steps=[f"Molar mass of {formula} = {_fmt(mass)} g/mol.",
               f"m = n \u00d7 M = {_fmt(mol)} \u00d7 {_fmt(mass)} = {_fmt(grams)} g."],
    )


# ---------------------------------------------------------------------------
# public solver
# ---------------------------------------------------------------------------

_BALANCE_CUE = re.compile(r"\bbalance\b", re.IGNORECASE)
_ARROW = re.compile(r"->|=>|→|⟶|=")


def solve(text: str) -> MathResult | None:
    """Recognise and compute a chemistry problem, else ``None`` (never guesses)."""
    if not text or not text.strip():
        return None
    _load()

    # 1. balance a chemical equation
    m = _BALANCE_CUE.search(text)
    if m and _ARROW.search(text):
        eq = text[m.end():]
        eq = re.sub(r"^\s*(?:the\s+)?equation\s*:?", "", eq, flags=re.IGNORECASE)
        eq = eq.strip().rstrip("?").strip()
        balanced = balance_equation(eq)
        if balanced is None:
            return None
        lhs, rhs, coeffs = balanced
        pretty = _format_equation(lhs, rhs, coeffs)
        return MathResult(
            ok=True, intent="chemistry", verified=True, exact=pretty,
            steps=["Write the unbalanced equation.",
                   "Conserve each element: solve for the smallest whole-number coefficients.",
                   f"Balanced: {pretty}"],
        )

    # 2. molar / molecular / formula mass
    m = re.search(r"(?:molar|molecular|formula)\s+mass\s+(?:of\s+)?(.+)$", text, re.IGNORECASE) \
        or re.search(r"mass\s+of\s+one\s+mole\s+of\s+(.+)$", text, re.IGNORECASE)
    if m:
        formula = _resolve_formula(m.group(1))
        counts = parse_formula(formula)
        mass = molar_mass(formula)
        if counts is None or mass is None:
            return None
        return _molar_mass_result(formula, mass, counts)

    # 3. element lookups
    m = re.search(r"atomic\s+number\s+(?:of\s+)?(.+)$", text, re.IGNORECASE)
    if m:
        el = element(m.group(1))
        return _element_number_result(el) if el else None
    m = re.search(r"atomic\s+(?:mass|weight)\s+(?:of\s+)?(.+)$", text, re.IGNORECASE)
    if m:
        el = element(m.group(1))
        return _element_mass_result(el) if el else None
    m = re.search(r"(?:chemical\s+)?symbol\s+(?:for|of)\s+(.+)$", text, re.IGNORECASE)
    if m:
        el = element(m.group(1))
        return _symbol_result(el) if el else None
    m = re.search(r"(?:what|which)\s+element\s+(?:is|has\s+the\s+symbol)\s+(.+)$", text, re.IGNORECASE)
    if m:
        el = element(m.group(1))
        return _name_result(el) if el else None
    m = re.search(r"element\s+(?:with\s+atomic\s+number\s+|number\s+)?(\d+)$", text, re.IGNORECASE)
    if m:
        el = element(m.group(1))
        return _name_result(el) if el else None

    # 4. mole <-> mass conversions
    m = re.search(r"how\s+many\s+moles\s+(?:are\s+)?(?:in\s+|do\s+)?([\d.eE+-]+)\s*"
                  r"(?:g|grams?)\s+(?:of\s+)?(.+)$", text, re.IGNORECASE)
    if m:
        formula = _resolve_formula(m.group(2))
        mass = molar_mass(formula)
        if mass is None:
            return None
        try:
            grams = float(m.group(1))
        except ValueError:
            return None
        return _moles_from_mass_result(grams, formula, mass, grams / mass)
    m = re.search(r"(?:what\s+is\s+the\s+)?mass\s+of\s+([\d.eE+-]+)\s*(?:mol|moles)\s+"
                  r"(?:of\s+)?(.+)$", text, re.IGNORECASE) \
        or re.search(r"how\s+many\s+grams\s+(?:are\s+)?(?:in|of)\s+([\d.eE+-]+)\s*(?:mol|moles)\s+"
                     r"(?:of\s+)?(.+)$", text, re.IGNORECASE)
    if m:
        formula = _resolve_formula(m.group(2))
        mass = molar_mass(formula)
        if mass is None:
            return None
        try:
            mol = float(m.group(1))
        except ValueError:
            return None
        return _mass_from_moles_result(mol, formula, mass, mol * mass)

    return None
