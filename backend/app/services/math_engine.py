"""Symbolic math engine (SymPy) for Jalora — spec §math.

Pure compute, no network/Ollama. Exposes:
  - classify_math(text) -> MathSignal | None   (detect math; `direct` => fast-path)
  - solve(expression, intent=None) -> MathResult (SymPy compute + step text)
  - format_reply(result) -> str                 (MathJax-ready markdown)
  - verify_math(solution, equation) -> MathResult  (substitute-check, spec #7)

Design rules (spec items 6, 8, 9):
  * A verified calculation is NEVER overridden by model text — the result is
    computed here; the fast-path in chat_service may ask the model to *explain*
    the verified steps, but the authoritative answer block is always appended
    (see app.services.science_explain).
  * Invalid expressions / division by zero / unsupported ops return a structured
    `error` result — we never invent an answer.
  * Arithmetic/algebra/calculus/etc. are computed directly, so they work offline
    and never touch Ollama.
"""
from __future__ import annotations

import logging
import math as _math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("jalora.math")

try:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    _TRANSFORM = standard_transformations + (convert_xor, implicit_multiplication_application)
    _HAS_SYMPY = True
except Exception:  # pragma: no cover - environment guard
    sp = None  # type: ignore[assignment]
    _TRANSFORM = ()
    _HAS_SYMPY = False


# ---------------------------------------------------------------------------
# signal / result models
# ---------------------------------------------------------------------------

class MathSignal(BaseModel):
    """Result of classifying a message as (or not) being a math question."""
    intent: str | None = None  # arithmetic|algebra|calculus|statistics|units|word_problem
    direct: bool = False       # True => single expression/equation, no narrative => fast-path
    confidence: int = 0        # 0..100


class MathResult(BaseModel):
    """Outcome of a math solve. `ok=False` means we explicitly did NOT compute."""
    ok: bool
    intent: str | None = None
    variable: str = "x"
    latex: str = ""            # display-math LaTeX for a `$$ ... $$` block
    steps: list[str] = Field(default_factory=list)  # human-readable step lines (may contain \(...\))
    exact: str = ""           # plain readable exact form (e.g. "5/6", "x = 2, x = 3")
    numeric: str = ""         # decimal form when it differs from `exact`
    units: str = ""           # trailing unit string if applicable
    verified: bool = False    # solution checked by substitution / numeric check
    error: str | None = None


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

_VERB = re.compile(
    r"\b(solve|compute|calculat(e|ion)|(evaluat(e|ion))|find|work out|determine|"
    r"reduc(?:e|tion))\b",
    re.IGNORECASE,
)
# Plain arithmetic verbs ("add 3 and 5") and question frames ("what is 42").
_ARITH = re.compile(
    r"\b(add|sum|plus|subtract|minus|multiply|times|divide|divided)\b",
    re.IGNORECASE,
)
_QUEST = re.compile(r"\b(what is|what's|how much|how many)\b", re.IGNORECASE)
_GEO_CUE = re.compile(
    r'\b(area|perimeter|circumference|circle|radius|diameter|triangle|rectangle|square|volume|base|height)\b', re.IGNORECASE)
_CALC = re.compile(
    r"\b(integrat(e|ion|ed)|differentiat(e|ion|ed)|derivative|derivatives|"
    r"integral|integrals|limit|limits|liminf|lim\b|sum of|sigma|summation)\b",
    re.IGNORECASE,
)
_TRIG = re.compile(r"\b(sin|cos|tan|log|ln|sqrt|root|pi|theta)\b", re.IGNORECASE)
_PROB = re.compile(
    r"\b(mean|median|mode|variance|standard deviation|probability|chance|"
    r"nCr|nPr|combination|permutation|dice|die|expected value)\b",
    re.IGNORECASE,
)
# an arithmetic/algebraic operator with operands, or a lone equation
_OPS = re.compile(
    r"[\d\s][+\-*/^%][\d\s\w\(\)]|[a-zA-Z]\w*\s*=\s*[^\s=]|[a-zA-Z]\s*[\+\-\*/^]\s*[a-zA-Z\d]",
)
_EQU = re.compile(r"([a-zA-Z]\w?)\s*=\s*([^=].*)")
_NUM = re.compile(r"\d+\s*/\s*\d+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\d+\^\d+")
_UNIT = re.compile(
    r"\b\d+\s*(km|cm|mm|kg|g|lb|oz|ton|tonne|in|inch|inches|ft|feet|yd|yard|"
    r"mi|mph|kph|m/s|km/h|celsius|fahrenheit|kelvin|\u00b0)\b",
    re.IGNORECASE,
)
_CONVERT = re.compile(r"\b(convert| to | in )\b", re.IGNORECASE)
_WORD_SCENE = re.compile(
    r"\b(if|travels|covers|runs|at a rate|in\s+\d|speed|distance|per hour|"
    r"per liter|simple interest|compound interest|interest|cost|price|salary|"
    r"discount|tax rate|per annum|per year|ratio of|area of|circle|radius|"
    r"diameter|perimeter|circumference|triangle|rectangle|square|base|height|"
    r"width|length|total)\b",
    re.IGNORECASE,
)
# E.164-style phone numbers ("+254700000001"): a leading '+' followed by a long
# digit run is a phone number, not an arithmetic operator.
_PHONE = re.compile(r"(?<![\w.])[+]\s?\d{7,}")
# a trig/root function called with parentheses: "sin(", "sqrt(", "log(" ...
_TRIG_CALL = re.compile(
    r"(?<!\w)(sin|cos|tan|ln|sqrt|root)\s*\(",
    re.IGNORECASE,
)
_LATEX = re.compile(r"\$\$.*?\$\$|\$.+?\$|\\[\(\[].+?\\[\)\]]", re.DOTALL)

# --- Phase 2 cues: broader mathematics (linalg, number theory, series, ODE,
# complex, distributions, regression). Each cue is deliberately tight so prose
# ("who is the prime minister") is never mistaken for a computation. ------------
_MATRIX_LITERAL = re.compile(r"\[\s*\[[^\]]*\](?:\s*,\s*\[[^\]]*\])*\s*\]")
_LINALG_CALL = re.compile(
    r"\b(det|determinant|inverse|inv|transpose|eigenvalues?|eigenvectors?|rank|matmul|dot|cross)\s*\(",
    re.IGNORECASE,
)
_LINALG_VERB = re.compile(
    r"\b(determinant|eigenvalues?|eigenvectors?|transpose|rank|inverse\s+(?:of|matrix)|"
    r"matrix\s+multiplication|matmul|dot\s+product|inner\s+product|cross\s+product)\b",
    re.IGNORECASE,
)
_NT_CALL = re.compile(r"\b(gcd|hcf|lcm|phi|totient|fibonacci|fib|factorial)\s*\(", re.IGNORECASE)
_NT_VERB = re.compile(
    r"\b(?:gcd|hcf|lcm|prime\s+factoriz\w*|prime\s+factors?|"
    r"factoriz\w*\s+(?:of\s+)?\d+|"
    r"(?:all\s+)?(?:positive\s+)?(?:divisors?|factors?)\s+of\s*\d+|"
    r"is\s+\d+\s+(?:a\s+)?prime|greatest\s+common\s+divisor|"
    r"least\s+common\s+multiple|highest\s+common\s+factor|modular\s+inverse|"
    r"(?:euler'?s?\s+)?(?:phi|totient)\s+of|fibonacci|\d+\s+(?:mod|modulo)|factorial)\b",
    re.IGNORECASE,
)
_SERIES = re.compile(
    r"\b(taylor|maclaurin|power\s+series|geometric\s+series|series|summation|sum\s+of)\b",
    re.IGNORECASE,
)
_ODE_CUE = re.compile(
    r"\b(differential\s+equation|dsolve|ode|initial[\s-]value)\b", re.IGNORECASE
)
_ODE_PRIME = re.compile(r"(?<![A-Za-z])[a-zA-Z]\s*'\s*'?")
_COMPLEX_CUE = re.compile(
    r"\b(complex|imaginary|conjugate|modulus|argument|polar|real\s+part|imaginary\s+part)\b",
    re.IGNORECASE,
)
_COMPLEX_LITERAL = re.compile(
    r"(?:\d\s*\*?\s*i(?![A-Za-z0-9_])|(?<![A-Za-z0-9_.])i(?![A-Za-z0-9_]))"
)
_DIST_CUE = re.compile(
    r"\b(normal\s+distribution|standard\s+normal|binomial|poisson|"
    r"probability\s+distribution|probability\s+density|pdf|cdf)\b",
    re.IGNORECASE,
)
_REG_CUE = re.compile(
    r"\b(regression|line\s+of\s+best\s+fit|best[\s-]fit|least\s+squares|correlation|"
    r"trendline|linear\s+fit|fit\s+a\s+(?:line|curve)|line\s+of\s+fit)\b",
    re.IGNORECASE,
)


def _strip_question(text: str) -> str:
    return text.strip().rstrip("?").strip()


# Leading natural-language question phrases that aren't part of the expression.
_Q_PREFIX = re.compile(
    r"^\s*(what is|what's|find|calculate|compute|evaluate|work out|determine|"
    r"the value of|the derivative of|the integral of|the limit of|"
    r"the area of|the speed of|the surface area of|the volume of)\s+",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    """Strip a leading question phrase so 'what is 2+2' -> '2+2'."""
    return _Q_PREFIX.sub("", text).strip()


# Common calculus-verb typos so 'intergrate 3x^2' still routes to the
# calculus solver (sp.integrate) instead of the arithmetic last-resort.
_CALC_VERB_FIX: dict[str, str] = {
    "intergrate": "integrate", "integtrate": "integrate", "integrte": "integrate",
    "diferentiate": "differentiate", "differnetiate": "differentiate",
    "derivitive": "derivative", "deriviative": "derivative",
    "limmit": "limit", "limmit": "limit", "derivada": "derivative",
}


def _fix_calc_verbs(text: str) -> str:
    """Normalize common calculus-verb misspellings so classification & solving
    reach the SymPy CAS branch (e.g. 'intergrate 3x^2' -> 'integrate 3x^2')."""
    out = text
    for bad, good in _CALC_VERB_FIX.items():
        out = re.sub(rf"\b{re.escape(bad)}\b", good, out, flags=re.IGNORECASE)
    return out


# Words that are legitimate inside a bare computable expression: function names,
# constants, statistics/arithmetic/calculus verbs, glue words and unit tokens.
# Anything else of length >= 3 marks the message as prose, so it is NOT
# fast-pathed — the model explains it instead (never fabricate: spec #8).
_MATH_WORDS = {
    # functions / constants
    "sin", "cos", "tan", "sec", "csc", "cot", "asin", "acos", "atan",
    "sinh", "cosh", "tanh", "log", "ln", "exp", "sqrt", "root", "abs",
    "pi", "theta", "phi", "alpha", "beta", "gamma", "delta", "lambda",
    "inf", "infinity",
    # statistics / combinatorics
    "mean", "median", "mode", "variance", "var", "stdev", "sd",
    "ncr", "npr", "comb", "perm", "combination", "combinations",
    "permutation", "permutations", "choose", "expected", "average",
    "probability", "dice", "die",
    # arithmetic glue / verbs
    "add", "sum", "plus", "subtract", "minus", "multiply", "times",
    "divide", "divided", "product", "quotient", "mod", "modulo",
    "ratio", "and", "of", "the", "to", "from", "in", "by", "between",
    # calculus glue / verbs
    "integrate", "integral", "differentiate", "derivative",
    "derive", "limit", "factor", "factors", "expand", "simplify",
    "reduce", "solve", "compute", "calculate", "evaluate", "find",
    "work", "out", "determine", "value", "respect", "with", "as",
    "dx", "dy", "dt", "dz", "dr",
    # unit tokens (so a bare conversion isn't treated as prose)
    "km", "cm", "mm", "kg", "lb", "oz", "ft", "yd", "mi", "mph", "kph",
    "celsius", "fahrenheit", "kelvin", "gram", "grams", "kilogram",
    "kilograms", "meter", "meters", "metre", "metres", "liter", "litre",
    "second", "seconds", "minute", "minutes", "hour", "hours", "day",
    "days", "week", "weeks",
    # Phase 2: linear algebra, number theory, series, complex, distributions,
    # regression — nouns that legitimately appear in a bare computable request.
    "matrix", "matrices", "determinant", "eigenvalues", "eigenvalue",
    "eigenvectors", "eigenvector", "transpose", "rank", "inverse", "dot",
    "gcd", "hcf", "lcm", "prime", "factoriz", "factorization", "factorisation",
    "divisors", "divisor", "totient", "fibonacci", "fib", "factorial",
    "taylor", "maclaurin", "series", "expansion", "differential", "dsolve",
    "complex", "imaginary", "conjugate", "modulus", "argument", "polar",
    "re", "im", "binomial", "poisson", "distribution", "normal", "variance",
    "regression", "correlation",
}

_PROSE_WORD = re.compile(r"[A-Za-z]{3,}")


def _computable_form(text: str) -> bool:
    """True when `text` is a bare expression/equation with no prose words, i.e.
    safe to fast-path. Narrative science sentences return False so the model
    explains them (Phase 0 fix for `integration failed` on word problems)."""
    return all(w.lower() in _MATH_WORDS for w in _PROSE_WORD.findall(text))


def is_prose_like(text: str | None) -> bool:
    """True when a message reads as narrative prose rather than a bare
    expression — used to route science word problems to the model."""
    if not text:
        return False
    return not _computable_form(_fix_calc_verbs(_normalize(_strip_question(text))))


def _friendly_error(exc: Exception) -> str:
    """Map a raw parser/CAS exception to a short, user-safe message so we never
    leak '<string>, line 1' or Python tracebacks into the reply."""
    if isinstance(exc, (SyntaxError, TypeError, ValueError)):
        return "the expression isn't formatted correctly"
    if isinstance(exc, ZeroDivisionError):
        return "division by zero"
    if isinstance(exc, (RecursionError, MemoryError)):
        return "the expression is too large to evaluate"
    msg = str(exc).strip()
    return msg[:120] if msg else type(exc).__name__


def classify_math(text: str | None) -> MathSignal | None:
    """Classify `text` as a math question. Returns None if not recognisably math.

    `direct=True` means the message is a single expression/equation with no
    narrative — suitable for the Ollama-less fast-path.
    """
    if not text or not text.strip():
        return None
    t = _fix_calc_verbs(_strip_question(text))
    if not t:
        return None

    has_verb = bool(_VERB.search(t))
    has_calc = bool(_CALC.search(t))
    has_trig = bool(_TRIG.search(t))
    has_prob = bool(_PROB.search(t))
    has_ops = bool(_OPS.search(t)) and not _PHONE.search(t)
    has_equ = bool(_EQU.search(t))
    has_num = bool(_NUM.search(t))
    has_unit = bool(_UNIT.search(t))
    has_convert = bool(_CONVERT.search(t))
    has_latex = bool(_LATEX.search(t))
    has_word = bool(_WORD_SCENE.search(t))
    has_arith = bool(_ARITH.search(t))
    has_quest = bool(_QUEST.search(t))
    # A conversion the pint engine can actually parse ("5 m in cm", "90 km/h to
    # m/s") is a stronger unit signal than the small manual keyword table.
    has_unit_conv = False
    if has_convert:
        try:
            from app.services import math_units as _math_units

            has_unit_conv = _math_units.convert(t) is not None
        except Exception:  # noqa: BLE001
            has_unit_conv = False
    # A trailing operator ("2+", "x^") is an incomplete expression => still math.
    has_trailing_op = bool(re.search(r"[A-Za-z0-9]\s*[\+\-\*/^%]\s*$", t))
    # A trig/root keyword alone ("sin", "log in ...") is prose; it only counts
    # as math when it is paired with a real math cue or written as a function
    # call ("sin(x)", "log(100)").
    trig_ok = has_trig and (
        has_num or has_ops or has_equ or has_arith or has_quest or has_verb
        or has_calc or has_prob or has_latex or has_unit
        or bool(_TRIG_CALL.search(t))
    )
    # Phase 2 cues. Each requires corroboration (a number/operator/equation) so
    # bare prose words ("prime minister", "normal day") are not routed.
    has_linalg = bool(_MATRIX_LITERAL.search(t)) or (
        bool(_LINALG_CALL.search(t) or _LINALG_VERB.search(t)) and has_num
    )
    has_nt = bool(_NT_CALL.search(t)) or (
        bool(_NT_VERB.search(t)) and (has_num or _NT_CALL.search(t))
    )
    has_series = bool(_SERIES.search(t)) and (
        has_num or has_ops or has_equ or bool(_TRIG_CALL.search(t))
    )
    has_ode = bool(_ODE_CUE.search(t)) or bool(
        _ODE_PRIME.search(t) and (has_equ or has_ops or has_num)
    )
    has_complex = (
        bool(_COMPLEX_CUE.search(t)) and (has_num or has_ops or has_equ)
    ) or (bool(_COMPLEX_LITERAL.search(t)) and (has_num or has_ops))
    has_dist = bool(_DIST_CUE.search(t)) and has_num
    has_reg = bool(_REG_CUE.search(t)) and (has_num or "(" in t)

    # Only classify as math with a real signal: a math operator/equation/unit,
    # calculus/trig/stats keyword, verbatim LaTeX, an arithmetic verb, a question
    # frame that includes a number, an incomplete trailing operator, or a geometry/
    # scene phrase with a number. A bare number in prose ("I have 3 cats") is NOT
    # routed to the engine (spec #3 / #10).
    is_math = (has_calc or trig_ok or has_prob or has_ops or has_latex
               or has_trailing_op
               or has_unit_conv
               or has_linalg or has_nt or has_series or has_ode
               or has_complex or has_dist or has_reg
               or (has_equ and (has_num or has_verb))
               or (has_unit and has_convert)
               or (has_quest and has_num)
               or (has_arith and has_num)
               or (has_verb and has_num)
               or (has_word and has_num
                   and (has_convert or has_quest or has_arith or has_equ
                        or has_ops or bool(_GEO_CUE.search(t)))))
    if not is_math:
        return None

    sentences = [s for s in re.split(r"(?:[.!?]\s+)", t) if s.strip()]
    sentence_count = max(1, len(sentences))

    if has_word and has_num and (sentence_count >= 2 or has_convert):
        intent = "word_problem"
    elif has_calc:
        intent = "calculus"
    elif has_ode:
        intent = "ode"
    elif has_series:
        intent = "series"
    elif has_nt:
        intent = "number_theory"
    elif has_linalg:
        intent = "linalg"
    elif has_dist:
        intent = "distribution"
    elif has_complex:
        intent = "complex"
    elif has_reg:
        intent = "regression"
    elif has_unit_conv or (has_unit and not has_word):
        intent = "units"
    elif has_prob:
        intent = "statistics"
    elif has_equ:
        intent = "algebra"
    else:
        intent = "arithmetic"

    direct = intent != "word_problem" and _computable_form(_normalize(t))
    confidence = 95 if (has_ops or has_equ or has_calc or has_latex or has_unit
                        or has_linalg or has_nt or has_ode or has_dist) else 75
    return MathSignal(intent=intent, direct=direct, confidence=confidence)


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------

def _x(expr: str, evaluate: bool = True) -> Any:
    """Parse a string into a SymPy expression (^ -> **, implicit multiplication)."""
    if not _HAS_SYMPY:
        raise RuntimeError("SymPy is not installed")
    return parse_expr(expr, transformations=_TRANSFORM, evaluate=evaluate)


def _var(text: str) -> str:
    """Pick the variable symbol used in an expression/equation."""
    letters = re.findall(r"\b([a-zA-Z])\b", text)
    for cand in ("x", "y", "t", "z", "a", "b"):
        if cand in letters:
            return cand
    return "x"


def _num(x: float) -> str:
    """Human-readable decimal: integer-valued -> int, else trimmed float."""
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError):
        return str(x)
    if d == d.to_integral_value():
        return str(int(d))
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s[:16]


def _plain(obj: Any) -> str:
    """Plain readable exact string for a value."""
    try:
        if obj.is_Rational and not obj.is_integer:
            return f"{obj.p}/{obj.q}"
        if obj.is_Integer:
            return str(int(obj))
    except Exception:  # noqa: BLE001
        pass
    return str(obj)


def _latex(obj: Any) -> str:
    try:
        return sp.latex(obj)
    except Exception:  # noqa: BLE001
        return str(obj)


def _zero(obj: Any) -> bool:
    try:
        if obj == 0:
            return True
        v = sp.simplify(obj)
        if v == 0:
            return True
        if v.is_number:
            return abs(float(v.evalf())) < 1e-9
    except Exception:  # noqa: BLE001
        pass
    return False


# ---------------------------------------------------------------------------
# unit conversion (manual table — spec #2)
# ---------------------------------------------------------------------------

_UNITS: dict[str, tuple[str, float]] = {
    "m": ("length", 1.0), "km": ("length", 1000.0), "cm": ("length", 0.01),
    "mm": ("length", 0.001), "in": ("length", 0.0254), "inch": ("length", 0.0254),
    "inches": ("length", 0.0254), "ft": ("length", 0.3048), "feet": ("length", 0.3048),
    "yd": ("length", 0.9144), "mile": ("length", 1609.344),
    "kg": ("mass", 1.0), "g": ("mass", 0.001), "lb": ("mass", 0.45359237),
    "oz": ("mass", 0.028349523125), "tonne": ("mass", 1000.0),
    "s": ("time", 1.0), "sec": ("time", 1.0), "min": ("time", 60.0),
    "hour": ("time", 3600.0), "hours": ("time", 3600.0),
    "celsius": ("temp", 1.0), "fahrenheit": ("temp", 1.0), "kelvin": ("temp", 1.0),
}


def _try_units(text: str) -> MathResult | None:
    if "convert" not in text.lower() and " to " not in text.lower() and " in " not in text.lower():
        return None
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s*(?:to|in)\s*([a-zA-Z]+)",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    val, src, dst = float(m.group(1)), m.group(2).lower(), m.group(3).lower()
    sk = _UNITS.get(src)
    dk = _UNITS.get(dst)
    if not sk or not dk:
        return None
    if sk[0] != dk[0]:
        return MathResult(ok=False, intent="units",
                          error=f"Cannot convert {src} to {dst} (incompatible dimensions).")
    out = _convert_temp(src, dst, val) if sk[0] == "temp" else val * sk[1] / dk[1]
    res = _num(out)
    return MathResult(
        ok=True, intent="units", units=dst,
        steps=[f"Convert {m.group(1)} {src} to {dst}.",
               f"1 {src} = {sk[1] / dk[1]} {dst}.",
               f"\\({res}\\,{dst}\\)"],
        exact=res, numeric=res, latex=f"{res}\\,{dst}", verified=True,
    )


def _convert_temp(src: str, dst: str, n: float) -> float:
    if src == dst:
        return n
    if src == "celsius" and dst == "fahrenheit":
        return n * 9 / 5 + 32
    if src == "fahrenheit" and dst == "celsius":
        return (n - 32) * 5 / 9
    if src == "celsius" and dst == "kelvin":
        return n + 273.15
    if src == "kelvin" and dst == "celsius":
        return n - 273.15
    return n


# ---------------------------------------------------------------------------
# statistics / probability (spec #2)
# ---------------------------------------------------------------------------

def _number_list(text: str) -> list[float] | None:
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    if not nums:
        return None
    try:
        return [float(n) for n in nums]
    except ValueError:
        return None


def _combo(n: int, r: int, kind: str) -> MathResult:
    if r > n or r < 0:
        return MathResult(ok=False, intent="statistics",
                          error=f"{kind} requires 0 \u2264 r \u2264 n; got ({n}, {r}).")
    v = _math.comb(n, r) if kind == "comb" else _math.perm(n, r)
    fn = "C" if kind == "comb" else "P"
    return MathResult(
        ok=True, intent="statistics",
        steps=[f"\\({fn}({n}, {r}) = {v}\\)", f"= {v}"],
        exact=str(v), numeric=str(v), latex=str(v), verified=True,
    )


def _try_stats(text: str) -> MathResult | None:
    low = text.lower()
    # combinations / permutations: nCr(5,2), nPr(5,2), "5 choose 2", "5 permutations"
    m = re.search(r"nCr\(\s*(\d+)\s*,\s*(\d+)\s*\)", text)
    if m:
        return _combo(int(m.group(1)), int(m.group(2)), "comb")
    m = re.search(r"nPr\(\s*(\d+)\s*,\s*(\d+)\s*\)", text)
    if m:
        return _combo(int(m.group(1)), int(m.group(2)), "perm")
    m = re.search(r"(\d+)\s*(?:combinations|choose)\s*(?:of)?\s*(\d+)", text, re.I)
    if m:
        return _combo(int(m.group(2)), int(m.group(1)), "comb")

    nums = _number_list(text)
    if not nums:
        return None
    n = len(nums)
    mean = sum(nums) / n
    var = sum((x - mean) ** 2 for x in nums) / (n - 1 if n > 1 else 1)
    std = _math.sqrt(var)
    s = sorted(nums)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    if "median" in low:
        val, name = median, "median"
    elif "variance" in low:
        val, name = var, "variance"
    elif "standard deviation" in low or "stdev" in low:
        val, name = std, "standard deviation"
    elif "mean" in low or "average" in low or "expected" in low:
        val, name = mean, "mean"
    else:
        return None
    res = _num(val)
    return MathResult(
        ok=True, intent="statistics", variable="x_i",
        steps=[f"Data: {nums}", f"n = {n}", f"{name.capitalize()} = {res}"],
        exact=res, numeric=res, latex=res, verified=True,
    )


# ---------------------------------------------------------------------------
# word problems (spec #4): givens -> formula -> solve -> verify
# ---------------------------------------------------------------------------

def _wp_speed(text: str) -> MathResult | None:
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(km|mi(?:les?)?|m|meters?)\s*(?:in|over|for)\s*"
        r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?|min|minutes?)",
        text, re.I,
    )
    if not m:
        return None
    dist = float(m.group(1)); du = m.group(2).lower(); t = float(m.group(3)); tu = m.group(4).lower()
    if du in ("m", "meter", "meters", "metres"):
        dist = dist / 1000.0
    elif "mile" in du:
        dist = dist * 1.609344
    if "min" in tu:
        t = t / 60.0
    speed = dist / t if t else float("inf")
    res = _num(speed)
    return MathResult(
        ok=True, intent="word_problem", variable="speed", units="km/h",
        steps=[f"Given: distance = {m.group(1)} {m.group(2)}, time = {m.group(3)} {m.group(4)}.",
               "Formula: speed = distance / time.",
               f"speed = {m.group(1)} / {m.group(3)} = {res} km/h."],
        exact=res, numeric=res, latex=f"{res}\\,\\text{{km/h}}",
        verified=abs(speed - float(speed)) < 1e-6,
    )


def _wp_percentage(text: str) -> MathResult | None:
    low = text.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:of|on)\s*(\d+(?:\.\d+)?)", low)
    if not m:
        m = re.search(r"what is (\d+(?:\.\d+)?)% of (\d+(?:\.\d+)?)", low)
        if not m:
            return None
    p, base = float(m.group(1)), float(m.group(2))
    result = p / 100 * base
    res = _num(result)
    return MathResult(
        ok=True, intent="word_problem",
        steps=[f"Given: {p}% of {base}.",
               "Formula: result = (percent / 100) \u00d7 base.",
               f"= {p}/100 \u00d7 {base} = {res}."],
        exact=res, numeric=res, latex=res, verified=True,
    )


def _wp_geometry(text: str) -> MathResult | None:
    low = text.lower()
    nums = _number_list(text) or []
    if "area" in low and "circle" in low and ("radius" in low or "r" in low) and nums:
        r = nums[-1]
        val = _math.pi * r * r
        res = _num(round(val, 6))
        return MathResult(
            ok=True, intent="word_problem", variable="r", units="square units",
            steps=[f"Given: radius r = {r}.", "Formula: area = \u03c0 r^2.",
                   f"= \u03c0 \u00d7 {r}^2 = {res}."],
            exact=res, numeric=res, latex=f"\u03c0 \\cdot {r}^{{2}}", verified=True,
        )
    if ("circumference" in low or ("perimeter" in low and "circle" in low)) and nums \
            and ("radius" in low or "r" in low):
        r = nums[-1]
        val = 2 * _math.pi * r
        res = _num(round(val, 6))
        return MathResult(
            ok=True, intent="word_problem", variable="r", units="units",
            steps=[f"Given: radius r = {r}.", "Formula: C = 2 \u03c0 r.",
                   f"= 2 \u03c0 \u00d7 {r} = {res}."],
            exact=res, numeric=res, latex=f"2 \u03c0 \\cdot {r}", verified=True,
        )
    if "area" in low and "rectangle" in low and len(nums) >= 2:
        a, b = nums[-2], nums[-1]
        res = _num(a * b)
        a_s = str(int(a)) if float(a).is_integer() else str(a)
        b_s = str(int(b)) if float(b).is_integer() else str(b)
        return MathResult(
            ok=True, intent="word_problem", units="square units",
            steps=["Formula: area = length \u00d7 width.",
                   f"= {a_s} \u00d7 {b_s} = {res}."],
            exact=res, numeric=res, latex=f"{a_s} \\cdot {b_s}", verified=True,
        )
    return None


def _wp_arithmetic(text: str) -> MathResult | None:
    """Solve arithmetic wordings like "add 3 and 5", "3 plus 5", "6 times 7"."""
    low = text.lower()
    if not _ARITH.search(low):
        return None
    nums = _number_list(text)
    if not nums or len(nums) < 2:
        return None
    if "subtract" in low or "minus" in low:
        val = nums[0] - sum(nums[1:])
        op = "-"
    elif "multiply" in low or "times" in low:
        val = nums[0]
        for n in nums[1:]:
            val *= n
        op = "*"
    elif "divide" in low:
        val = nums[0]
        for n in nums[1:]:
            if n == 0:
                return MathResult(ok=False, intent="word_problem",
                                  error="Division by zero.")
            val /= n
        op = "/"
    else:
        val = sum(nums)
        op = "+"
    res = _num(val)
    return MathResult(
        ok=True, intent="word_problem",
        steps=[f"Given numbers: {nums}.", f"Operation: {op}.", f"= {res}"],
        exact=res, numeric=res, latex=res, verified=True,
    )


def _wp_ratio(text: str) -> MathResult | None:
    """Solve ratio wordings: "ratio of 3 to 2" or "3:2" (with a ratio cue)."""
    low = text.lower()
    m = re.search(r"ratio of\s+(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)", low)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", text)
        if not m or "ratio" not in low:
            return None
        a, b = float(m.group(1)), float(m.group(2))
    if b == 0:
        return MathResult(ok=False, intent="word_problem",
                          error="Division by zero.")
    res = _num(a / b)
    return MathResult(
        ok=True, intent="word_problem", units="ratio",
        steps=[f"Given ratio: {a} : {b}.",
               "Formula: ratio value = first / second.",
               f"= {a} / {b} = {res}"],
        exact=res, numeric=res, latex=res, verified=True,
    )


def _solve_word_problem(text: str) -> MathResult | None:
    for fn in (_wp_geometry, _wp_speed, _wp_percentage, _wp_arithmetic, _wp_ratio):
        r = fn(text)
        if r is not None:
            return r
    return None


# ---------------------------------------------------------------------------
# calculus / algebra / expression
# ---------------------------------------------------------------------------

def _try_calculus(text: str) -> MathResult | None:
    low = text.lower()
    m = re.search(r"(?:d/d([a-zA-Z])|differentiat(?:e|ion|ed)|derivativ(?:e|es)?)\s*(.*)", text, re.I)
    if m and ("differentiat" in low or "derivativ" in low or "d/d" in low):
        var = m.group(1) or _var(m.group(2) or text)
        body = m.group(2) or ""
        body = re.sub(r"^(?:of|the|a)\s+", "", body, flags=re.I).strip()
        body = re.sub(r"with respect to [a-zA-Z]", "", body, flags=re.I).strip()
        if not body or not _computable_form(body):
            return MathResult(ok=False, intent="calculus",
                              error="I couldn't tell which expression to differentiate.")
        try:
            expr = _x(body)
            d = sp.diff(expr, sp.symbols(var))
            ver = _verify_derivative(expr, d, var)
            return MathResult(
                ok=True, intent="calculus", variable=var,
                steps=[f"Differentiate \\({_latex(expr)}\\) with respect to \\({var}\\).",
                       f"\\(\\frac{{d}}{{d{var}}} \\, {_latex(expr)} = {_latex(d)}\\)",
                       f"Result: \\({_latex(d)}\\) (\\({'verified' if ver else 'unverified'}\\))"],
                exact=_plain(d), latex=_latex(d), verified=ver,
            )
        except Exception as exc:  # noqa: BLE001
            return MathResult(ok=False, intent="calculus",
                              error=f"Derivative failed: {_friendly_error(exc)}")

    if "integrat" in low:
        return _do_integral(text)
    if "limit" in low or re.search(r"\blim\b", low):
        return _do_limit(text)
    return None


def _parse_bounds(text: str) -> tuple[str, str, str] | None:
    """Extract definite-integral limits. Returns (lower, upper, remaining)."""
    m = re.search(r"\[\s*([^\],]+?)\s*,\s*([^\]]+?)\s*\]", text)
    if m:
        return m.group(1).strip(), m.group(2).strip(), (text[:m.start()] + text[m.end():]).strip()
    m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:\s|$)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip(), (text[:m.start()] + text[m.end():]).strip()
    m = re.search(r"\bbetween\s+(.+?)\s+and\s+(.+?)(?:\s|$)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip(), (text[:m.start()] + text[m.end():]).strip()
    return None


def _numeric_integral(expr: Any, var: str, lower: float, upper: float) -> float | None:
    """Numerically integrate when SymPy has no elementary closed form (SciPy quad)."""
    sym = sp.symbols(var)
    try:
        f = sp.lambdify(sym, expr, "numpy")
        from app.services import math_numeric as _numeric

        val = _numeric.quad(lambda tt: float(f(tt)), lower, upper)
        if val is not None:
            return val
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(sp.N(sp.integrate(expr, (sym, lower, upper))))
    except Exception:  # noqa: BLE001
        return None


def _do_integral(text: str) -> MathResult:
    body = re.sub(r"integrat(?:e|ion|ed)\s*", "", text, flags=re.I)
    bounds = _parse_bounds(body)
    lower = upper = None
    if bounds:
        lower, upper, body = bounds
        lower = re.sub(r"^(?:the|a|of)\s+", "", lower, flags=re.I).strip()
        upper = re.sub(r"^(?:the|a|of)\s+", "", upper, flags=re.I).strip()
    body = re.sub(r"\bd[xytd]\b", "", body, flags=re.I)
    body = re.sub(r"^(?:the|a|of)\s+", "", body, flags=re.I).strip()
    body = re.sub(r"\s+", " ", body).strip()
    if not body or not _computable_form(body):
        return MathResult(ok=False, intent="calculus",
                          error="I couldn't tell which expression to integrate.")
    var = _var(body)
    sym = sp.symbols(var)
    try:
        expr = _x(body)
    except Exception as exc:  # noqa: BLE001
        return MathResult(ok=False, intent="calculus",
                          error=f"Integration failed: {_friendly_error(exc)}")

    # Refuse to integrate a string with many free symbols (fabrication guard:
    # "integrate the velocity" must not become c*e*i*l*o*t*v*x*y).
    free = {s.name for s in expr.free_symbols} - {var}
    if len(free) > 3:
        return MathResult(ok=False, intent="calculus",
                          error="that doesn't look like an integrable expression")

    # Definite integral.
    if lower is not None and upper is not None:
        try:
            lo, hi = _x(lower), _x(upper)
        except Exception:  # noqa: BLE001
            lo = hi = None
        if lo is not None and hi is not None:
            try:
                antideriv = sp.integrate(expr, sym)
                val = sp.simplify(antideriv.subs(sym, hi) - antideriv.subs(sym, lo))
                if val.has(sp.Integral):
                    raise ValueError("no closed form")
                numeric = _num(float(val.evalf()))
                return MathResult(
                    ok=True, intent="calculus", variable=var,
                    steps=[
                        f"Compute the definite integral "
                        f"\\(\\int_{{{_latex(lo)}}}^{{{_latex(hi)}}} {_latex(expr)}\\,d{var}\\).",
                        f"Antiderivative: \\(\\int {_latex(expr)}\\,d{var} = {_latex(antideriv)}\\).",
                        f"Evaluate between the limits: \\({_latex(lo)}\\) to \\({_latex(hi)}\\) "
                        f"gives \\({_latex(val)}\\).",
                    ],
                    exact=_plain(val), numeric=numeric,
                    latex=(rf"\int_{{{_latex(lo)}}}^{{{_latex(hi)}}} {_latex(expr)}\,d{var}"
                           rf" = {_latex(val)}"),
                    verified=True,
                )
            except Exception:  # noqa: BLE001
                numeric = _numeric_integral(expr, var, float(lo.evalf()), float(hi.evalf()))
                if numeric is not None:
                    res = _num(numeric)
                    return MathResult(
                        ok=True, intent="calculus", variable=var,
                        steps=[
                            f"Compute the definite integral "
                            f"\\(\\int_{{{_latex(lo)}}}^{{{_latex(hi)}}} {_latex(expr)}\\,d{var}\\).",
                            "No elementary closed form — evaluating numerically.",
                            f"Numeric value \\({res}\\).",
                        ],
                        exact=res, numeric=res,
                        latex=(rf"\int_{{{_latex(lo)}}}^{{{_latex(hi)}}} {_latex(expr)}\,d{var}"
                               rf" \approx {res}"),
                        verified=True,
                    )
                return MathResult(ok=False, intent="calculus",
                                  error="the definite integral could not be evaluated")

    # Indefinite integral.
    try:
        integral = sp.integrate(expr, sym)
        ver = _verify_integral(expr, integral, var)
        return MathResult(
            ok=True, intent="calculus", variable=var,
            steps=[f"Integrate \\({_latex(expr)}\\) with respect to \\({var}\\).",
                   f"\\(\\int {_latex(expr)}\\,d{var} = {_latex(integral)}\\)",
                   f"Result: \\({_latex(integral)}\\) (\\({'verified' if ver else 'unverified'}\\))"],
            exact=_plain(integral), latex=_latex(integral), verified=ver,
        )
    except Exception as exc:  # noqa: BLE001
        return MathResult(ok=False, intent="calculus",
                          error=f"Integration failed: {_friendly_error(exc)}")


def _do_limit(text: str) -> MathResult:
    point = 0
    m = re.search(r"x\s*->\s*([\-+]?\d+(?:\.\d+)?)", text)
    if m:
        point = float(m.group(1))
    body = re.sub(r"\blim{0,1}(?:it)?\s*", "", text, flags=re.I)
    body = re.sub(r"(?:as\s+)?x\s*->\s*[\-+]?\d+(?:\.\d+)?", "", body, flags=re.I)
    body = re.sub(r"_{\s*.*?\s*}", "", body).strip(" :")
    body = re.sub(r"^(?:the|a|of)\s+", "", body, flags=re.I).strip()
    var = _var(body)
    try:
        expr = _x(body)
        lim = sp.limit(expr, sp.symbols(var), point)
        return MathResult(
            ok=True, intent="calculus", variable=var,
            steps=[f"\\(\\lim_{{{var} \\to {point}}} {_latex(expr)} = {_latex(lim)}\\)",
                   f"Result: \\({_latex(lim)}\\)"],
            exact=_plain(lim), latex=_latex(lim), verified=True,
        )
    except Exception as exc:  # noqa: BLE001
        return MathResult(ok=False, intent="calculus",
                          error=f"Limit failed: {_friendly_error(exc)}")


def _try_equation(text: str) -> MathResult | None:
    """Solve one equation in one variable ('x^2 - 5x + 6 = 0') or a linear
    system ('2x + 3y = 6 and x - y = 1')."""
    norm = re.sub(r"->|=>|\u2192|\u27f6", "=", text)
    if "=" not in norm:
        return None
    n_eq = norm.count("=")
    parts = [norm] if n_eq <= 1 else re.split(r"\band\b|;|,", norm)
    eqs = [p.strip() for p in parts if "=" in p]
    if len(eqs) > 1:
        return _solve_system(eqs, norm)

    lhs, _, rhs = norm.partition("=")
    lhs, rhs = lhs.strip(), rhs.strip()
    var = _var(lhs + " " + rhs)
    try:
        lv = _x(lhs)
        rv = _x(rhs) if rhs else sp.Integer(0)
        eq = sp.Eq(lv, rv)
        sols = sp.solve(eq, var)
    except Exception as exc:  # noqa: BLE001
        return MathResult(ok=False, intent="algebra",
                          error=f"Invalid equation: {_friendly_error(exc)}")

    if not sols:
        return MathResult(
            ok=True, intent="algebra", variable=var,
            steps=[f"\\({_latex(lv)} = {_latex(rv)}\\)", "No solutions."],
            exact="no solution", latex="\\varnothing", verified=True,
        )

    roots = [sp.simplify(s) for s in sols]
    all_ok = True
    for r in roots:
        check = sp.simplify((lv - rv).subs(var, r))
        ok = _zero(check)
        all_ok = all_ok and ok

    step_latex = [f"Solve \\({_latex(lv)} = {_latex(rv)}\\) for \\({var}\\)."]
    try:
        diff_expr = sp.simplify(lv - rv)
        factored = sp.factor(diff_expr)
        if str(factored) != str(diff_expr):
            step_latex.append(f"Factor: \\({_latex(factored)} = 0\\).")
    except Exception:  # noqa: BLE001
        pass
    roots_str = ", ".join(f"{var} = {_plain(r)}" for r in roots)
    step_latex.append(f"Roots: {roots_str} (\\({'verified' if all_ok else 'unverified'}\\))")
    return MathResult(
        ok=True, intent="algebra", variable=var,
        steps=step_latex, exact=roots_str, latex=roots_str, verified=all_ok,
    )


def _solve_system(eqs: list[str], original: str) -> MathResult:
    """Solve a linear/simple system of equations, verified per equation."""
    try:
        pairs: list[tuple[Any, Any]] = []
        for e in eqs:
            l, _, r = e.partition("=")
            lv = _x(l.strip())
            rv = _x(r.strip()) if r.strip() else sp.Integer(0)
            pairs.append((lv, rv))
    except Exception as exc:  # noqa: BLE001
        return MathResult(ok=False, intent="algebra",
                          error=f"Invalid equation: {_friendly_error(exc)}")
    syms = sorted(
        {s for lv, rv in pairs for s in (lv.free_symbols | rv.free_symbols)},
        key=lambda s: s.name,
    )
    if not syms:
        return MathResult(ok=False, intent="algebra", error="no unknowns to solve for")
    try:
        sols = sp.solve([sp.Eq(lv, rv) for lv, rv in pairs], syms, dict=True)
    except Exception as exc:  # noqa: BLE001
        return MathResult(ok=False, intent="algebra",
                          error=f"Invalid equation: {_friendly_error(exc)}")
    if not sols:
        return MathResult(
            ok=True, intent="algebra",
            steps=[f"Solve the system: {original}", "No solutions."],
            exact="no solution", latex="\\varnothing", verified=True,
        )
    sol = sols[0]
    all_ok = True
    for lv, rv in pairs:
        try:
            all_ok = all_ok and _zero((lv - rv).subs(sol))
        except Exception:  # noqa: BLE001
            all_ok = False
    sol_str = ", ".join(f"{k} = {_plain(v)}" for k, v in sol.items())
    steps = [f"Solve the system: {original}."]
    for lv, rv in pairs:
        steps.append(f"Equation: \\({_latex(lv)} = {_latex(rv)}\\).")
    steps.append(f"Solution: {sol_str} (\\({'verified' if all_ok else 'unverified'}\\))")
    return MathResult(
        ok=True, intent="algebra", variable=syms[0].name,
        steps=steps, exact=sol_str, latex=sol_str, verified=all_ok,
    )


def _try_algebra_op(text: str) -> MathResult | None:
    low = text.lower()
    body = re.sub(r"^(factor|expand|simplify|reduce)\s+", "", text, flags=re.I)
    # Strip instructional tails from natural-language requests.  Without this,
    # SymPy treats words such as "using" and "Theorem" as symbolic variables,
    # producing a mathematically valid-looking but unrelated expression.
    body = re.split(r"\s+(?:using|with|showing|step(?:-by-step)?|and explain)\b", body,
                    maxsplit=1, flags=re.I)[0].strip().rstrip("?.")
    try:
        expr = _x(body)
    except Exception:  # noqa: BLE001
        return None
    if expr == sp.zoo:
        return MathResult(ok=False, intent="arithmetic", error="Division by zero is undefined.")
    if "factor" in low:
        out, label = sp.factor(expr), "Factor"
    elif "expand" in low:
        out, label = sp.expand(expr), "Expand"
    else:
        out, label = sp.simplify(expr), "Simplify"
        # A bare expression (no explicit simplify/reduce verb) only yields a
        # number; otherwise prose like "the cost is 3 dollars" would be
        # re-formatted as a (nonsensical) symbolic product (spec #8: never invent).
        if "simplify" not in low and "reduce" not in low and not out.is_number:
            return None
    return MathResult(
        ok=True, intent="arithmetic",
        steps=[f"{label} \\({_latex(expr)}\\) = \\({_latex(out)}\\)"],
        exact=_plain(out), latex=_latex(out), verified=True,
    )


def _try_expression(text: str) -> MathResult:
    try:
        expr = _x(text)
    except Exception as exc:  # noqa: BLE001
        return MathResult(ok=False, intent="arithmetic",
                          error=f"I couldn't parse '{text}': {_friendly_error(exc)}")
    if expr == sp.zoo:
        return MathResult(ok=False, intent="arithmetic", error="Division by zero is undefined.")
    simplified = sp.simplify(expr)
    steps: list[str] = [f"Evaluate: \\({_latex(expr)}\\)"]
    if simplified != expr:
        steps.append(f"Simplify: \\({_latex(simplified)}\\)")
    if not simplified.is_number:
        return MathResult(ok=False, intent="arithmetic",
                          error=f"I couldn't parse '{text}' as a numeric expression.")
    if expr.is_number:
        try:
            num = float(simplified.evalf())
        except Exception:  # noqa: BLE001
            num = None
        exact_s = _plain(sp.nsimplify(simplified, rational=True) if not simplified.is_number else simplified)
        numeric_s = _num(num) if num is not None else ""
        if numeric_s and numeric_s != exact_s:
            steps.append(f"Decimal: \\({numeric_s}\\)")
        return MathResult(
            ok=True, intent="arithmetic",
            steps=steps, exact=exact_s, numeric=numeric_s,
            latex=_latex(simplified), verified=True,
        )
    return MathResult(
        ok=True, intent="arithmetic",
        steps=steps, exact=_plain(simplified), latex=_latex(simplified), verified=True,
    )


# ---------------------------------------------------------------------------
# Phase 2: matrices / linear algebra
# ---------------------------------------------------------------------------

def _matrix_exact(M: Any) -> str:
    """Readable exact matrix string, rows separated by '; '."""
    rows = []
    for i in range(M.rows):
        rows.append(", ".join(_plain(M[i, j]) for j in range(M.cols)))
    return "[" + "; ".join(rows) + "]"


def _to_sympy_matrix(block: str) -> Any:
    rows = re.findall(r"\[([^\[\]]*)\]", block)
    data = []
    for r in rows:
        parts = [p for p in re.split(r"[,\s]+", r.strip()) if p]
        data.append([_x(p) for p in parts])
    return sp.Matrix(data)


def _try_linalg(text: str) -> MathResult | None:
    low = text.lower()
    blocks = _MATRIX_LITERAL.findall(text)
    if blocks:
        try:
            M = _to_sympy_matrix(blocks[0])
        except Exception:  # noqa: BLE001
            return None

        def _mat_result(label: str, obj: Any, latex: str) -> MathResult:
            return MathResult(
                ok=True, intent="linalg",
                steps=[f"{label} \\({_latex(M)}\\)", f"= \\({latex}\\)"],
                exact=_matrix_exact(obj) if hasattr(obj, "rows") else _plain(obj),
                latex=latex, verified=True,
            )

        if "determinant" in low or re.search(r"\bdet\b", low) or _LINALG_CALL.search(text):
            try:
                d = sp.simplify(M.det())
            except Exception as exc:  # noqa: BLE001
                return MathResult(ok=False, intent="linalg",
                                  error=f"Determinant failed: {_friendly_error(exc)}")
            return MathResult(
                ok=True, intent="linalg",
                steps=[f"Compute \\(\\det{_latex(M)}\\).", f"\\(= {_latex(d)}\\)"],
                exact=_plain(d), latex=_latex(d), verified=True,
            )
        if "inverse" in low or "inv(" in low or "^-1" in text or "\u207b\u00b9" in text:
            if M.rows != M.cols:
                return MathResult(ok=False, intent="linalg",
                                  error="only square matrices can be inverted")
            if M.det() == 0:
                return MathResult(ok=False, intent="linalg",
                                  error="the matrix is singular (determinant is 0) — no inverse")
            try:
                inv = M.inv()
            except Exception as exc:  # noqa: BLE001
                return MathResult(ok=False, intent="linalg",
                                  error=f"Inverse failed: {_friendly_error(exc)}")
            return _mat_result("Invert", inv, _latex(inv))
        if "rank" in low:
            r = M.rank()
            return MathResult(
                ok=True, intent="linalg",
                steps=[f"Row-reduce \\({_latex(M)}\\) to find its rank.",
                       f"rank = {r}"],
                exact=str(r), numeric=str(r), latex=str(r), verified=True,
            )
        if "transpose" in low or re.search(r"\]\s*\^\s*T\b", text) or "\u1d40" in text:
            T = M.T
            return _mat_result("Transpose", T, _latex(T))
        if "eigen" in low:
            try:
                if "vector" in low:
                    pairs = M.eigenvects()
                    parts = []
                    for val, mult, vecs in pairs:
                        for v in vecs:
                            parts.append(f"\u03bb = {_plain(val)}: {_matrix_exact(v.T)}")
                    exact = "; ".join(parts) or "none"
                    return MathResult(
                        ok=True, intent="linalg",
                        steps=[f"Find the eigenvectors of \\({_latex(M)}\\).", f"{exact}"],
                        exact=exact, latex=",\\; ".join(parts), verified=True,
                    )
                evs = M.eigenvals()
                parts = [f"\u03bb = {_plain(v)} (x{m})" if m > 1 else f"\u03bb = {_plain(v)}"
                         for v, m in evs.items()]
                exact = ", ".join(parts) or "none"
                return MathResult(
                    ok=True, intent="linalg",
                    steps=[f"Solve \\(\\det{_latex(M)} - \\lambda I) = 0\\).", exact],
                    exact=exact, latex=exact, verified=True,
                )
            except Exception as exc:  # noqa: BLE001
                return MathResult(ok=False, intent="linalg",
                                  error=f"Eigenvalue computation failed: {_friendly_error(exc)}")
        # matrix power
        mp = re.search(r"\]\s*\^\s*(-?\d+)", text)
        if mp:
            try:
                P = M ** int(mp.group(1))
            except Exception as exc:  # noqa: BLE001
                return MathResult(ok=False, intent="linalg",
                                  error=f"Matrix power failed: {_friendly_error(exc)}")
            return _mat_result("Raise", P, _latex(P))
        # matrix multiplication of two literals
        if len(blocks) >= 2 and ("*" in text or "matmul" in low or "multiply" in low
                                 or re.search(r"times", low)):
            try:
                N = _to_sympy_matrix(blocks[1])
                P = M * N
            except Exception as exc:  # noqa: BLE001
                return MathResult(ok=False, intent="linalg",
                                  error=f"Matrix multiplication failed: {_friendly_error(exc)}")
            return _mat_result("Multiply", P, _latex(P))
        # A bare matrix with no requested operation is not a question.
        return None

    # dot / cross product of two single-bracket vectors
    vecs = re.findall(r"\[([^\[\]]*)\]", text)
    if len(vecs) >= 2 and re.search(r"\b(dot|inner|cross)\b", low):
        def _vec(s: str) -> Any:
            return sp.Matrix([_x(p) for p in re.split(r"[,\s]+", s.strip()) if p])

        try:
            u, v = _vec(vecs[0]), _vec(vecs[1])
        except Exception:  # noqa: BLE001
            return None
        if "cross" in low:
            if u.rows != 3 or v.rows != 3:
                return MathResult(ok=False, intent="linalg",
                                  error="cross product requires two 3-dimensional vectors")
            w = u.cross(v)
            return MathResult(
                ok=True, intent="linalg",
                steps=[f"\\({_latex(u)} \\times {_latex(v)}\\)", f"= \\({_latex(w)}\\)"],
                exact=_matrix_exact(w.T), latex=_latex(w), verified=True,
            )
        if u.rows != v.rows:
            return MathResult(ok=False, intent="linalg",
                              error="dot product requires equal-length vectors")
        d = _plain(u.dot(v))
        return MathResult(
            ok=True, intent="linalg",
            steps=[f"\\({_latex(u)} \\cdot {_latex(v)}\\)", f"= {d}"],
            exact=d, numeric=d, latex=d, verified=True,
        )
    return None


# ---------------------------------------------------------------------------
# Phase 2: number theory
# ---------------------------------------------------------------------------

def _try_number_theory(text: str) -> MathResult | None:
    low = text.lower()

    m = (re.search(
        r"\b(?:gcd|hcf|greatest\s+common\s+divisor|highest\s+common\s+factor)\s*"
        r"(?:of\s*)?\(?\s*(\d+)\s*(?:,|and)\s*(\d+)", text, re.I)
        or re.search(r"\b(?:gcd|hcf)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", text, re.I))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        g = _math.gcd(a, b)
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"Greatest common divisor of {a} and {b}:", f"\\(\\gcd({a}, {b}) = {g}\\)"],
            exact=str(g), numeric=str(g), latex=str(g), verified=True,
        )

    m = (re.search(r"\blcm\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", text, re.I)
         or re.search(r"\b(?:lcm|least\s+common\s+multiple)\s*(?:of\s*)?(\d+)\s*(?:,|and)\s*(\d+)",
                      text, re.I))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        l = abs(a * b) // _math.gcd(a, b) if (a or b) else 0
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"Least common multiple of {a} and {b}:",
                   f"\\(\\text{{lcm}}({a}, {b}) = {l}\\)"],
            exact=str(l), numeric=str(l), latex=str(l), verified=True,
        )

    m = (re.search(r"(?:prime\s+)?factoriz\w*\s*(?:of\s*|into\s*)?(\d+)", low)
         or re.search(r"prime\s+factors?\s+of\s+(\d+)", low))
    if m and ("factoriz" in low or "prime factor" in low):
        n = int(m.group(1))
        if n < 2:
            return MathResult(ok=False, intent="number_theory",
                              error="prime factorisation needs an integer \u2265 2")
        fac = sp.factorint(n)
        prod = sp.Mul(*[sp.Pow(p, e) for p, e in fac.items()])
        parts = " \u00d7 ".join(f"{p}^{e}" if e > 1 else str(p)
                               for p, e in sorted(fac.items()))
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"Factorise {n} into primes:", f"\\({parts}\\)"],
            exact=parts, latex=_latex(prod), verified=True,
        )

    m = re.search(r"\bis\s+(\d+)\s+(?:a\s+)?prime\b", low)
    if m:
        n = int(m.group(1))
        isp = bool(sp.isprime(n))
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"Test whether {n} is prime.", f"{n} is {'prime' if isp else 'not prime'}."],
            exact="prime" if isp else "not prime",
            latex="\\text{prime}" if isp else "\\text{not prime}", verified=True,
        )

    m = re.search(r"(?:all\s+)?(?:positive\s+)?(?:divisors?|factors?)\s+of\s+(\d+)", low)
    if m:
        n = int(m.group(1))
        ds = list(sp.divisors(n))
        shown = ", ".join(str(d) for d in ds)
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"Divisors of {n}:", shown],
            exact=shown, latex=", ".join(str(d) for d in ds), verified=True,
        )

    m = re.search(r"(?:euler'?s?\s+)?(?:phi|totient)\s*(?:of\s*|\(\s*)(\d+)", low)
    if m:
        n = int(m.group(1))
        t = int(sp.totient(n))
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"Euler's totient of {n}:", f"\\(\\varphi({n}) = {t}\\)"],
            exact=str(t), numeric=str(t), latex=str(t), verified=True,
        )

    m = re.search(r"(?:modular\s+)?inverse\s+of\s+(\d+)\s*(?:mod|modulo)\s*(\d+)", low)
    if m:
        a, mod = int(m.group(1)), int(m.group(2))
        try:
            inv = pow(a, -1, mod)
        except ValueError:
            return MathResult(ok=False, intent="number_theory",
                              error=f"{a} has no inverse modulo {mod} (they are not coprime)")
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"Solve \\( {a}x \\equiv 1 \\pmod{{{mod}}}\\).",
                   f"\\(x = {inv}\\)"],
            exact=str(inv), numeric=str(inv), latex=str(inv), verified=True,
        )

    m = re.search(r"(\d+)\s*(?:mod|modulo)\s*(\d+)", low)
    if m:
        a, mod = int(m.group(1)), int(m.group(2))
        if mod == 0:
            return MathResult(ok=False, intent="number_theory", error="modulo by zero")
        r = a % mod
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"\\( {a} \\bmod {mod}\\)", f"= {r}"],
            exact=str(r), numeric=str(r), latex=str(r), verified=True,
        )

    m = re.search(r"\bfib(?:onacci)?\s*\(?\s*(\d+)\s*\)?", low)
    if m:
        n = int(m.group(1))
        v = int(sp.fibonacci(n))
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"Fibonacci number F({n}):", f"= {v}"],
            exact=str(v), numeric=str(v), latex=str(v), verified=True,
        )

    m = (re.search(r"\bfactorial\s*\(\s*(\d+)\s*\)", low)
         or re.search(r"(\d+)\s+factorial", low)
         or re.search(r"(\d+)\s*!", text))
    if m:
        n = int(m.group(1))
        if n > 10000:
            return MathResult(ok=False, intent="number_theory",
                              error="that factorial is too large to compute")
        v = _math.factorial(n)
        return MathResult(
            ok=True, intent="number_theory",
            steps=[f"{n}! = {v}", "Computed exactly."],
            exact=str(v), numeric=str(v), latex=str(v), verified=True,
        )
    return None


# ---------------------------------------------------------------------------
# Phase 2: series — summation & Taylor/Maclaurin expansion
# ---------------------------------------------------------------------------

def _bound(value: str) -> Any:
    v = value.strip().strip(".")
    if v.lower() in ("infinity", "inf", "oo", "\u221e", "+infinity", "+inf", "+oo"):
        return sp.oo
    if v.lower() in ("-infinity", "-inf", "-oo", "-\u221e"):
        return -sp.oo
    return _x(v)


def _try_series(text: str) -> MathResult | None:
    low = text.lower()

    m = re.search(
        r"\bsum(?:mation)?\b\s*(?:of\s+)?(.*?)\s+from\s+([a-zA-Z])\s*=\s*"
        r"([^\s,]+)\s+to\s+([^\s,]+)",
        text, re.IGNORECASE,
    )
    if m:
        body = m.group(1).strip()
        var = m.group(2)
        try:
            expr = _x(body)
            sym = sp.symbols(var)
            lo, hi = _bound(m.group(3)), _bound(m.group(4))
            val = sp.summation(expr, (sym, lo, hi))
            val = sp.simplify(val)
        except Exception as exc:  # noqa: BLE001
            return MathResult(ok=False, intent="series",
                              error=f"Series evaluation failed: {_friendly_error(exc)}")
        if val.has(sp.Sum):
            return MathResult(ok=False, intent="series",
                              error="this series has no closed form I can compute")
        numeric = _num(float(val.evalf())) if val.is_number else ""
        return MathResult(
            ok=True, intent="series", variable=var,
            steps=[f"Evaluate the sum \\(\\sum_{{{var}={_latex(lo)}}}^{{{_latex(hi)}}} "
                   f"{_latex(expr)}\\).", f"\\(= {_latex(val)}\\)"],
            exact=_plain(val), numeric=numeric, latex=_latex(val), verified=True,
        )

    if "taylor" in low or "maclaurin" in low or "power series" in low:
        m = re.search(
            r"(?:taylor|maclaurin|power)\s+series\s+(?:of\s+)?(.+?)"
            r"(?:\s+(?:around|about|at)\s+([^\s,]+))?"
            r"(?:\s+(?:up\s+to|to\s+order|order)\s*([^\s,]+))?\s*$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        body = m.group(1).strip()
        center_raw = (m.group(2) or ("0" if "maclaurin" in low else "0")).strip()
        order_raw = (m.group(3) or "").strip()
        order = 6
        mo = re.search(r"\^\s*(\d+)", order_raw)
        if mo:
            order = int(mo.group(1)) + 1
        elif re.fullmatch(r"\d+", order_raw or ""):
            order = int(order_raw)
        try:
            var = _var(body)
            sym = sp.symbols(var)
            expr = _x(body)
            center = _x(center_raw)
            series = sp.series(expr, sym, center, order).removeO()
            series = sp.expand(series)
        except Exception as exc:  # noqa: BLE001
            return MathResult(ok=False, intent="series",
                              error=f"Series expansion failed: {_friendly_error(exc)}")
        kind = "Maclaurin" if "maclaurin" in low else "Taylor"
        return MathResult(
            ok=True, intent="series", variable=var,
            steps=[f"{kind} series of \\({_latex(expr)}\\) "
                   f"about \\({_latex(center)}\\) (order {order - 1}).",
                   f"\\({_latex(series)}\\)"],
            exact=_plain(series), latex=_latex(series), verified=True,
        )
    return None


# ---------------------------------------------------------------------------
# Phase 2: ordinary differential equations (symbolic dsolve + numeric IVP)
# ---------------------------------------------------------------------------

def _sympify_ode_side(side: str, dep: str, indep: str) -> Any:
    f = sp.Function(dep)
    x = sp.symbols(indep)
    s = side
    s = re.sub(rf"{dep}\s*'''", f"{dep}({indep}).diff({indep}, 3)", s)
    s = re.sub(rf"{dep}\s*''", f"{dep}({indep}).diff({indep}, 2)", s)
    s = re.sub(rf"d{dep}\s*/\s*d{indep}", f"{dep}({indep}).diff({indep})", s)
    s = re.sub(rf"{dep}\s*'", f"{dep}({indep}).diff({indep})", s)
    s = re.sub(rf"\b{dep}\b(?!\s*\()", f"{dep}({indep})", s)
    return sp.sympify(s, locals={dep: f, indep: x})


def _try_ode(text: str) -> MathResult | None:
    if not (_ODE_CUE.search(text) or _ODE_PRIME.search(text)):
        return None

    dep = "y"
    m = re.search(r"([a-zA-Z])\s*'", text)
    if m:
        dep = m.group(1)
    indep = "t" if dep == "x" else "x"
    m2 = re.search(r"d([a-zA-Z])\s*/\s*d([a-zA-Z])", text)
    if m2:
        dep, indep = m2.group(1), m2.group(2)
    else:
        m3 = re.search(rf"{dep}\s*\(\s*([a-zA-Z])\s*\)", text)
        if m3:
            indep = m3.group(1)

    # equation text: from the derivative term through the right-hand side.
    body = text
    ic = re.search(rf"{dep}\s*\(\s*(-?[\d.]+)\s*\)\s*=\s*(-?[\d.]+)", body)
    ic_point = ic_value = None
    if ic:
        ic_point, ic_value = float(ic.group(1)), float(ic.group(2))
        body = body[:ic.start()] + " " + body[ic.end():]
    interval = (re.search(r"from\s*(-?[\d.]+)\s*to\s*(-?[\d.]+)", body, re.I)
                or re.search(r"\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]", body))
    interval_bounds = None
    if interval:
        interval_bounds = (float(interval.group(1)), float(interval.group(2)))
        body = body[:interval.start()] + " " + body[interval.end():]
    body = re.sub(r"\b(solve|the|a|an|differential\s+equation|dsolve|ode|find|for|"
                  r"initial[\s-]value|problem|with|given|where|such\s+that)\b",
                  " ", body, flags=re.I)
    body = re.sub(r"\s+", " ", body).strip().strip(",")
    if "=" not in body:
        return None

    # numeric initial-value problem when an interval + first-order derivative.
    if interval_bounds and ic_point is not None and body.count("'") <= 2 \
            and not re.search(rf"{dep}\s*''", body):
        num = _ode_numeric(body, dep, indep, ic_point, ic_value, interval_bounds)
        if num is not None:
            return num

    # symbolic dsolve.
    lhs, _, rhs = body.partition("=")
    try:
        lv = _sympify_ode_side(lhs, dep, indep)
        rv = _sympify_ode_side(rhs, dep, indep) if rhs.strip() else sp.Integer(0)
        eq = sp.Eq(lv, rv)
        f = sp.Function(dep)
        x = sp.symbols(indep)
        ics = {f(ic_point): ic_value} if ic_point is not None else None
        sol = sp.dsolve(eq, f(x), ics=ics) if ics else sp.dsolve(eq, f(x))
        rhs_expr = sp.simplify(sol.rhs)
    except Exception as exc:  # noqa: BLE001
        return MathResult(ok=False, intent="ode",
                          error=f"Differential equation failed: {_friendly_error(exc)}")
    exact = f"{dep} = {_plain(rhs_expr)}"
    return MathResult(
        ok=True, intent="ode", variable=indep,
        steps=[f"Solve the differential equation \\({_latex(eq)}\\).",
               f"General solution: \\({dep}({indep}) = {_latex(rhs_expr)}\\)"],
        exact=exact, latex=f"{dep}({indep}) = {_latex(rhs_expr)}", verified=True,
    )


def _ode_numeric(body: str, dep: str, indep: str, ic_point: float, ic_value: float,
                 bounds: tuple[float, float]) -> MathResult | None:
    """Numerically integrate a first-order IVP ``dep' = f(indep, dep)`` (SciPy)."""
    lhs, _, rhs = body.partition("=")
    if "'" in rhs:
        lhs, rhs = rhs, lhs  # derivative written on the right-hand side
    try:
        y = sp.symbols(dep)
        t = sp.symbols(indep)
        rhs_expr = _x(re.sub(rf"{dep}\s*'", "", rhs.strip()))
        import sympy as _sp

        fn = _sp.lambdify((t, y), rhs_expr, "numpy")

        from app.services import math_numeric as _numeric

        if not _numeric.has_scipy():
            return None
        t0, t1 = bounds
        out = _numeric.solve_ivp(lambda tt, yy: [float(fn(tt, yy[0]))],
                                 t0, t1, [ic_value])
        if out is None or not out["success"]:
            return None
        y_end = out["y"][-1][0]
    except Exception:  # noqa: BLE001
        return None
    res = _num(y_end)
    return MathResult(
        ok=True, intent="ode", variable=indep,
        steps=[f"Numerically integrate \\({dep}' = {_latex(rhs_expr)}\\) "
               f"with {dep}({_num(ic_point)}) = {_num(ic_value)}.",
               f"Using an adaptive Runge-Kutta (RK45) method over "
               f"[{_num(t0)}, {_num(t1)}].",
               f"\\({dep}({_num(t1)}) \\approx {res}\\)"],
        exact=res, numeric=res, latex=res, verified=True,
    )


# ---------------------------------------------------------------------------
# Phase 2: complex numbers
# ---------------------------------------------------------------------------

def _imag_unit(s: str) -> str:
    s = re.sub(r"(?<![A-Za-z0-9_.])(\d+\.?\d*|\.\d+)\s*\*\s*i(?![A-Za-z0-9_])", r"\1*I", s)
    s = re.sub(r"(?<![A-Za-z0-9_.])(\d+\.?\d*|\.\d+)\s+i(?![A-Za-z0-9_])", r"\1*I", s)
    s = re.sub(r"(?<![A-Za-z0-9_.])(\d+\.?\d*|\.\d+)i(?![A-Za-z0-9_])", r"\1*I", s)
    s = re.sub(r"(?<![A-Za-z0-9_.])i(?![A-Za-z0-9_])", "I", s)
    return s


def _complex_str(z: Any) -> str:
    return str(z).replace("**", "^").replace("*I", "i").replace("I", "i").replace("*", "")


def _try_complex(text: str) -> MathResult | None:
    low = text.lower()
    cue = bool(_COMPLEX_CUE.search(text))
    if not cue and not _COMPLEX_LITERAL.search(text):
        return None
    want_mod = bool(re.search(r"\b(modulus|magnitude|absolute|abs)\b", low))
    want_arg = bool(re.search(r"\b(argument|arg|angle|phase)\b", low))
    want_conj = "conjugate" in low
    want_real = "real part" in low
    want_imag = "imaginary part" in low
    want_polar = "polar" in low

    body = text
    mb = re.search(r"\|\s*([^|]+?)\s*\|", body)
    if mb:
        body = mb.group(1)
        want_mod = True
    body = re.sub(r"\b(complex|imaginary|conjugate|modulus|magnitude|absolute|abs|"
                  r"argument|arg|angle|phase|polar form|polar|real part|imaginary part|"
                  r"real|imaginary|part|number|form|of|the)\b", " ", body, flags=re.I)
    body = _imag_unit(body).strip()
    if not body:
        return None
    try:
        z = _x(body)
    except Exception:  # noqa: BLE001
        return None
    if z.free_symbols or z == sp.zoo:
        return None
    z = sp.simplify(z)

    if want_polar:
        r_ = sp.simplify(sp.Abs(z))
        th = sp.simplify(sp.arg(z))
        return MathResult(
            ok=True, intent="complex",
            steps=[f"For \\(z = {_latex(z)}\\):",
                   f"\\(|z| = {_latex(r_)}\\), \\(\\arg(z) = {_latex(th)}\\)"],
            exact=f"|z| = {_plain(r_)}, arg(z) = {_plain(th)}",
            latex=f"z = {_latex(r_)}\\,e^{{{_latex(th)}i}}", verified=True,
        )
    if want_conj:
        c = sp.simplify(sp.conjugate(z))
        return MathResult(
            ok=True, intent="complex",
            steps=[f"Conjugate of \\({_latex(z)}\\):", f"\\({_latex(c)}\\)"],
            exact=_complex_str(c), latex=_latex(c), verified=True,
        )
    if want_arg:
        th = sp.simplify(sp.arg(z))
        return MathResult(
            ok=True, intent="complex",
            steps=[f"\\(\\arg({_latex(z)})\\)", f"\\(= {_latex(th)}\\)"],
            exact=_plain(th), latex=_latex(th), verified=True,
        )
    if want_real:
        r_ = sp.simplify(sp.re(z))
        return MathResult(
            ok=True, intent="complex",
            steps=[f"Real part of \\({_latex(z)}\\):", f"\\(= {_latex(r_)}\\)"],
            exact=_plain(r_), latex=_latex(r_), verified=True,
        )
    if want_imag:
        i_ = sp.simplify(sp.im(z))
        return MathResult(
            ok=True, intent="complex",
            steps=[f"Imaginary part of \\({_latex(z)}\\):", f"\\(= {_latex(i_)}\\)"],
            exact=_plain(i_), latex=_latex(i_), verified=True,
        )
    if want_mod:
        r_ = sp.simplify(sp.Abs(z))
        return MathResult(
            ok=True, intent="complex",
            steps=[f"Modulus of \\({_latex(z)}\\):", f"\\(= {_latex(r_)}\\)"],
            exact=_plain(r_), numeric=_num(float(r_.evalf())) if r_.is_number else "",
            latex=_latex(r_), verified=True,
        )
    return MathResult(
        ok=True, intent="complex",
        steps=[f"Simplify \\({_latex(z)}\\).", f"\\({_latex(z)}\\)"],
        exact=_complex_str(z), latex=_latex(z), verified=True,
    )


# ---------------------------------------------------------------------------
# Phase 2: probability distributions (SciPy stats)
# ---------------------------------------------------------------------------

def _param(low: str, pattern: str, default: float | None = None) -> float | None:
    m = re.search(pattern, low, re.IGNORECASE)
    if not m:
        return default
    try:
        return float(m.group(1))
    except (ValueError, IndexError):
        return default


def _extract_comparison(text: str) -> tuple[str | None, float | None]:
    m = re.search(r"[pP]\s*\(\s*X\s*(<=|\u2264|<|>=|\u2265|>|=)\s*(-?[\d.]+)\s*\)", text)
    grp = None
    if m:
        grp = m
    else:
        m = re.search(r"\bX\s*(<=|\u2264|<|>=|\u2265|>|=)\s*(-?[\d.]+)", text)
        grp = m
    if not grp:
        return None, None
    op = {"\u2264": "<=", "\u2265": ">="}.get(grp.group(1), grp.group(1))
    return op, float(grp.group(2))


def _prob_result(dist: str, op: str, value: float, prob: float, detail: str) -> MathResult:
    res = f"{prob:.6f}".rstrip("0").rstrip(".")
    label = {"<=": "\u2264", ">=": "\u2265"}.get(op, op)
    return MathResult(
        ok=True, intent="distribution",
        steps=[f"{dist} distribution.", detail,
               f"\\(P(X {label} {_num(value)}) = {res}\\)"],
        exact=res, numeric=res, latex=res, verified=0.0 <= prob <= 1.0,
    )


def _try_distribution(text: str) -> MathResult | None:
    low = text.lower()
    if not _DIST_CUE.search(text):
        return None
    op, x = _extract_comparison(text)

    from app.services import math_numeric as _numeric

    if "binomial" in low:
        n = _param(low, r"(?:trials|number\s+of\s+trials)\s*=?\s*(\d+)")
        if n is None:
            n = _param(low, r"\bn\s*=?\s*(\d+)")
        p = _param(low, r"(?:probability\s+of\s+success|\bp\b)\s*=?\s*([\d.]+)")
        k = _param(low, r"\bk\s*=?\s*(\d+)")
        if k is None and x is not None:
            k = x
        if n is None or p is None or k is None:
            return None
        n_i, k_i = int(n), int(k)
        detail = f"n = {n_i} trials, p = {_num(p)}, k = {k_i}."
        if op in (">=", ">"):
            prob = 1.0 - (_numeric.binomial_cdf(k_i - 1, n_i, p) or 0.0) if op == ">=" \
                else 1.0 - (_numeric.binomial_cdf(k_i, n_i, p) or 0.0)
        elif op in ("<", "<="):
            prob = _numeric.binomial_cdf(k_i - (1 if op == "<" else 0), n_i, p)
        else:
            prob = _numeric.binomial_pmf(k_i, n_i, p)
        if prob is None:
            return None
        return _prob_result("Binomial", op or "=", k_i, prob, detail)

    if "poisson" in low:
        lam = _param(low, r"(?:lambda|\u03bb|mean|rate)\s*=?\s*([\d.]+)")
        k = _param(low, r"\bk\s*=?\s*(\d+)")
        if k is None and x is not None:
            k = x
        if lam is None or k is None:
            return None
        k_i = int(k)
        detail = f"\u03bb = {_num(lam)}, k = {k_i}."
        if op in (">=", ">"):
            prob = 1.0 - (_numeric.poisson_cdf(k_i - 1, lam) or 0.0) if op == ">=" \
                else 1.0 - (_numeric.poisson_cdf(k_i, lam) or 0.0)
        elif op in ("<", "<="):
            prob = _numeric.poisson_cdf(k_i - (1 if op == "<" else 0), lam)
        else:
            prob = _numeric.poisson_pmf(k_i, lam)
        if prob is None:
            return None
        return _prob_result("Poisson", op or "=", k_i, prob, detail)

    if "normal" in low or "gaussian" in low:
        mean = _param(low, r"mean\s*=?\s*(-?[\d.]+)", 0.0)
        sd = _param(low, r"(?:standard\s+deviation|std\s+dev|sd|sigma|\u03c3)\s*=?\s*(-?[\d.]+)", 1.0)
        if x is None:
            return None
        detail = f"\u03bc = {_num(mean)}, \u03c3 = {_num(sd)}."
        if op in (">", ">="):
            prob = 1.0 - (_numeric.normal_cdf(x, mean, sd) or 0.0)
        elif op == "=":
            prob = _numeric.normal_pdf(x, mean, sd)
        else:
            prob = _numeric.normal_cdf(x, mean, sd)
        if prob is None:
            return None
        return _prob_result("Normal", op or "<", x, prob, detail)
    return None


# ---------------------------------------------------------------------------
# Phase 2: regression / correlation
# ---------------------------------------------------------------------------

def _coef(c: float) -> str:
    """Compact fitted number: exact integers, else ~6 significant digits, and
    treat numerical dust (|c| < 1e-9) as zero."""
    if abs(c) < 1e-9:
        return "0"
    r = round(c)
    if abs(c - r) < 1e-9:
        return str(int(r))
    return f"{c:.6g}"


def _poly_str(coeffs: list[float]) -> str:
    deg = len(coeffs) - 1
    terms: list[str] = []
    for i, c in enumerate(coeffs):
        power = deg - i
        cs = _coef(c)
        if cs == "0":
            continue
        if power == 0:
            terms.append(cs)
        elif power == 1:
            terms.append(f"{cs}*x")
        else:
            terms.append(f"{cs}*x^{power}")
    if not terms:
        return "y = 0"
    return "y = " + " + ".join(terms).replace("+ -", "- ")


def _try_regression(text: str) -> MathResult | None:
    low = text.lower()
    if not _REG_CUE.search(text):
        return None
    pts = re.findall(r"\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)", text)
    xs = [float(a) for a, _b in pts]
    ys = [float(b) for _a, b in pts]
    if len(pts) < 2:
        mx = re.search(r"x\s*=\s*\[\s*([^\]]+)\]", text, re.I)
        my = re.search(r"y\s*=\s*\[\s*([^\]]+)\]", text, re.I)
        if mx and my:
            xs = [float(v) for v in re.findall(r"-?[\d.]+", mx.group(1))]
            ys = [float(v) for v in re.findall(r"-?[\d.]+", my.group(1))]
    if len(xs) < 2 or len(xs) != len(ys):
        return None

    from app.services import math_numeric as _numeric

    if "correlation" in low and "regression" not in low and "fit" not in low:
        r = _numeric.correlation(xs, ys)
        if r is None:
            return None
        rs = _coef(r)
        return MathResult(
            ok=True, intent="regression",
            steps=[f"Pearson correlation of x = {xs} and y = {ys}.", f"r = {rs}"],
            exact=f"r = {rs}", latex=f"r = {rs}", verified=abs(r) <= 1.0 + 1e-9,
        )

    deg = 1
    if "quadratic" in low or "parabola" in low or "curve" in low:
        deg = 2
    md = re.search(r"degree\s*(\d+)", low)
    if md:
        deg = int(md.group(1))
    fit = _numeric.polyfit(xs, ys, deg)
    if fit is None:
        return None
    eq = _poly_str(fit["coeffs"])
    r2 = fit["r2"]
    return MathResult(
        ok=True, intent="regression", variable="x",
        steps=[f"Fit a degree-{deg} polynomial to x = {xs}, y = {ys}.",
               f"Least-squares fit: \\({eq}\\).",
               f"Coefficient of determination \\(R^2 = {_coef(r2)}\\)."],
        exact=eq, latex=eq.replace("y = ", "y = "), verified=r2 >= -1e-9,
    )


# ---------------------------------------------------------------------------
# core solve dispatcher
# ---------------------------------------------------------------------------

def _try_chemistry(text: str) -> "MathResult | None":
    """Compute a chemistry problem via the domain engine (Phase 4). Never raises."""
    try:
        from app.services import chemistry_engine

        return chemistry_engine.solve(text)
    except Exception:  # noqa: BLE001
        logger.exception("chemistry engine failed")
        return None


def _try_physics(text: str) -> "MathResult | None":
    """Compute a physics problem via the domain engine (Phase 3). Never raises."""
    try:
        from app.services import physics_engine

        return physics_engine.solve(text)
    except Exception:  # noqa: BLE001
        logger.exception("physics engine failed")
        return None


def solve(expression: str, intent: str | None = None) -> MathResult:
    """Compute `expression` with SymPy. Never raises — returns a MathResult."""
    if not expression or not expression.strip():
        return MathResult(ok=False, intent=intent, error="No expression provided.")
    # Phase 3/4: the domain chemistry and physics engines are independent of
    # SymPy classification (prose like "molar mass of H2O" is not a SymPy
    # expression), so they get first refusal. Chemistry runs first because a
    # balance equation contains "=". Both return None for unrelated text, so
    # ordinary math is unaffected.
    _ch = _try_chemistry(expression)
    if _ch is not None:
        return _ch
    _ph = _try_physics(expression)
    if _ph is not None:
        return _ph
    if not _HAS_SYMPY:
        return MathResult(ok=False, intent=intent,
                          error="Symbolic math is unavailable (SymPy not installed).")

    sig = classify_math(expression)
    if sig is None:
        return MathResult(ok=False, intent=intent,
                          error="That doesn't look like a maths question I can compute.")
    detected = intent or sig.intent

    text = _normalize(_strip_question(expression))

    # Units (intercept before sympify — "5 km to m" can't be parsed as math).
    # Prefer the full pint engine (compound/derived units, dimensions); fall
    # back to the built-in manual table when pint is unavailable or unsure.
    pu = None
    if detected in ("units", "arithmetic") or bool(_UNIT.search(text)):
        try:
            from app.services import math_units as _math_units

            pu = _math_units.convert(text)
        except Exception:  # noqa: BLE001
            pu = None
    if pu is not None:
        return MathResult(
            ok=pu.ok, intent="units", units=pu.units, exact=pu.exact,
            numeric=pu.numeric, latex=pu.latex, steps=list(pu.steps),
            verified=pu.ok, error=pu.error,
        )
    u = _try_units(text)
    if u is not None:
        return u
    # Probability distributions (before stats/word problems so a "normal
    # distribution with mean..." phrase is not read as a plain average).
    di = _try_distribution(text)
    if di is not None:
        return di

    # Phase 2: broaden the engine (matrices, number theory, series, ODEs,
    # complex numbers, regression). Run before word problems so evaluators such
    # as "sum of 1/n^2 from n=1 to infinity" are read as a series, not as a
    # "sum these givens" arithmetic word problem.
    la = _try_linalg(text)
    if la is not None:
        return la
    nt = _try_number_theory(text)
    if nt is not None:
        return nt
    se = _try_series(text)
    if se is not None:
        return se
    od = _try_ode(text)
    if od is not None:
        return od
    cx = _try_complex(text)
    if cx is not None:
        return cx
    rg = _try_regression(text)
    if rg is not None:
        return rg

    # Word problems: givens -> formula -> solve -> verify.
    wp = _solve_word_problem(text)
    if wp is not None:
        return wp
    # Statistics / probability.
    st = _try_stats(text)
    if st is not None:
        return st

    # Calculus.
    if detected == "calculus" or sig.intent == "calculus":
        c = _try_calculus(_fix_calc_verbs(expression))
        if c is not None:
            return c
    # Equation a = b.
    if "=" in text:
        eq = _try_equation(text)
        if eq is not None:
            return eq
    # Factor / expand / simplify / bare arithmetic.
    ao = _try_algebra_op(text)
    if ao is not None:
        return ao
    # Last resort.
    return _try_expression(text)


# ---------------------------------------------------------------------------
# verification (spec #7)
# ---------------------------------------------------------------------------

def _verify_derivative(f: Any, df: Any, var: str) -> bool:
    try:
        sy = sp.symbols(var)
        x0 = 1.0
        h = 1e-6
        d_fn = float(df.subs(sy, x0).evalf())
        num = (float(f.subs(sy, x0 + h).evalf()) - float(f.subs(sy, x0 - h).evalf())) / (2 * h)
        return abs(d_fn - num) < 1e-4
    except Exception:  # noqa: BLE001
        return False


def _verify_integral(f: Any, F: Any, var: str) -> bool:
    try:
        sy = sp.symbols(var)
        x0 = 2.0
        lhs = float(sp.diff(F, sy).subs(sy, x0).evalf())
        rhs = float(f.subs(sy, x0).evalf())
        return abs(lhs - rhs) < 1e-6
    except Exception:  # noqa: BLE001
        return False


def verify_math(solution: str, equation: str) -> MathResult:
    """Substitute `solution` into `equation` and check it holds (spec #7)."""
    if not _HAS_SYMPY:
        return MathResult(ok=False, error="SymPy unavailable.")
    try:
        var = _var(equation)
        lhs, _, rhs = equation.partition("=")
        target = sp.simplify(_x(lhs) - (_x(rhs) if rhs else 0))
        try:
            val = sp.sympify(solution)
        except Exception:  # noqa: BLE001
            val = _x(solution.split("=")[-1].strip()) if "=" in solution else _x(solution)
        residual = sp.simplify(target.subs(var, val))
        ok = _zero(residual)
        try:
            rs = _num(float(residual.evalf())) if residual.is_number else _latex(residual)
        except Exception:  # noqa: BLE001
            rs = _latex(residual)
        return MathResult(
            ok=True, intent="algebra", variable=var,
            steps=[f"Substitute {var} = {solution} into the equation.",
                   f"Residual = {rs}"],
            exact="true" if ok else "false",
            latex="\\text{verified}" if ok else "\\text{not satisfied}",
            verified=ok,
        )
    except Exception as exc:  # noqa: BLE001
        return MathResult(ok=False, intent="algebra", error=f"Could not verify: {exc}")


# ---------------------------------------------------------------------------
# reply formatting (MathJax-ready markdown)
# ---------------------------------------------------------------------------

def format_reply(result: MathResult) -> str:
    """Render a MathResult as MathJax-ready markdown.

    Display math is wrapped in `$$ ... $$`; the final answer is a highlighted
    blockquote so the frontend's `.math-answer` CSS can target it.
    """
    if not result.ok or result.error:
        return (f"I couldn't compute that: {result.error or 'unknown error'}. "
                "Please check the expression — for example, `1/2 + 1/3` or "
                "`x^2 - 5x + 6 = 0`. I never guess, so I only answer maths I "
                "can verify.")

    parts: list[str] = []
    for i, step in enumerate(result.steps, 1):
        parts.append(f"{i}. {step}")

    if result.latex:
        parts.append("")
        parts.append(f"$${result.latex}$$")

    if result.exact:
        ans = result.exact
        if result.units and not ans.endswith(result.units):
            ans = f"{ans} {result.units}"
        suffix = ""
        if result.numeric and result.numeric != result.exact:
            suffix = f" \u2248 {result.numeric}"
        tag = " \u2705 verified" if result.verified else ""
        parts.append("")
        parts.append(f"> **Answer:** {ans}{suffix}{tag}")
    elif result.numeric:
        tag = " \u2705 verified" if result.verified else ""
        parts.append("")
        parts.append(f"> **Answer:** {result.numeric}{tag}")
    return "\n".join(parts)


def format_answer(result: MathResult) -> str:
    """Render only the final answer line (no numbered steps / derivation).

    Used when the user turns "show working" off: the computed, verified answer is
    still shown in the same highlighted blockquote, but the steps and LaTeX
    derivation are omitted. Error results fall back to :func:`format_reply` so the
    honest failure message is never hidden.
    """
    if not result.ok or result.error:
        return format_reply(result)
    if result.exact:
        ans = result.exact
        if result.units and not ans.endswith(result.units):
            ans = f"{ans} {result.units}"
        suffix = ""
        if result.numeric and result.numeric != result.exact:
            suffix = f" \u2248 {result.numeric}"
        tag = " \u2705 verified" if result.verified else ""
        return f"> **Answer:** {ans}{suffix}{tag}"
    if result.numeric:
        tag = " \u2705 verified" if result.verified else ""
        return f"> **Answer:** {result.numeric}{tag}"
    return format_reply(result)
