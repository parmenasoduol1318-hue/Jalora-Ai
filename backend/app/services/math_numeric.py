"""Numeric engine (SciPy + NumPy) for Jalora — Phase 2.

Complements the symbolic :mod:`app.services.math_engine` with numeric methods
where a closed form is impractical or where a decimal answer is what the user
wants:

* numeric integration (:func:`quad`) for integrands with no elementary form;
* initial-value ODE integration (:func:`solve_ivp`);
* least-squares regression (:func:`polyfit`) and correlation;
* probability distributions (normal / binomial / Poisson) via ``scipy.stats``;
* numeric root finding (:func:`root_brentq`).

Import-light and fully offline: if SciPy or NumPy are missing,
:func:`is_available` is ``False`` and callers fall back to SymPy or a pure
Python implementation. Every public function is exception-guarded and returns
``None`` (or a structured dict with ``ok=False``) rather than raising, so the
caller can produce an honest "cannot compute" answer instead of a traceback.
"""
from __future__ import annotations

import logging
import math
from typing import Callable, Sequence

logger = logging.getLogger("jalora.numeric")

try:  # pragma: no cover - environment guard
    import numpy as np

    _HAS_NUMPY = True
except Exception:  # noqa: BLE001
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

try:  # pragma: no cover - environment guard
    from scipy import integrate as _integrate
    from scipy import optimize as _optimize
    from scipy import stats as _stats

    _HAS_SCIPY = True
except Exception:  # noqa: BLE001
    _integrate = None  # type: ignore[assignment]
    _optimize = None  # type: ignore[assignment]
    _stats = None  # type: ignore[assignment]
    _HAS_SCIPY = False


def is_available() -> bool:
    return _HAS_NUMPY or _HAS_SCIPY


def has_numpy() -> bool:
    return _HAS_NUMPY


def has_scipy() -> bool:
    return _HAS_SCIPY


# ---------------------------------------------------------------------------
# integration / roots / optimisation
# ---------------------------------------------------------------------------

def quad(fn: Callable[[float], float], lower: float, upper: float) -> float | None:
    """Definite integral of ``fn`` on ``[lower, upper]`` via adaptive quadrature."""
    if not _HAS_SCIPY:
        return None
    try:
        value, _err = _integrate.quad(lambda t: float(fn(t)), float(lower), float(upper))
        return float(value)
    except Exception:  # noqa: BLE001
        return None


def root_brentq(fn: Callable[[float], float], lower: float, upper: float) -> float | None:
    """A root of ``fn`` inside ``[lower, upper]`` (sign change required)."""
    if not _HAS_SCIPY:
        return None
    try:
        return float(_optimize.brentq(lambda t: float(fn(t)), float(lower), float(upper)))
    except Exception:  # noqa: BLE001
        return None


def solve_ivp(
    fn: Callable[[float, list[float]], Sequence[float]],
    t0: float,
    t1: float,
    y0: Sequence[float],
    *,
    t_eval: Sequence[float] | None = None,
    rtol: float = 1e-7,
    atol: float = 1e-9,
) -> dict | None:
    """Integrate ``dy/dt = fn(t, y)`` from ``t0`` to ``t1`` (RK45).

    ``fn`` receives ``(t, y_list)`` and returns a sequence. Returns a dict with
    ``t`` / ``y`` lists and ``success``, or ``None`` when SciPy is unavailable
    or the integration fails.
    """
    if not _HAS_SCIPY:
        return None

    def _rhs(t: float, y):
        return np.asarray(list(fn(float(t), list(np.atleast_1d(y)))), dtype=float)

    try:
        sol = _integrate.solve_ivp(
            _rhs, (float(t0), float(t1)), np.asarray(list(y0), dtype=float),
            t_eval=None if t_eval is None else np.asarray(list(t_eval), dtype=float),
            method="RK45", rtol=rtol, atol=atol, dense_output=True,
        )
        return {
            "success": bool(sol.success),
            "message": str(sol.message),
            "t": [float(v) for v in np.atleast_1d(sol.t)],
            "y": [[float(v) for v in np.atleast_1d(row)] for row in np.atleast_2d(sol.y).T],
        }
    except Exception:  # noqa: BLE001
        return None


def minimize_scalar(fn: Callable[[float], float], lower: float, upper: float) -> tuple[float, float] | None:
    """Local minimiser of ``fn`` on ``[lower, upper]`` -> ``(x, f(x))``."""
    if not _HAS_SCIPY:
        return None
    try:
        res = _optimize.minimize_scalar(lambda t: float(fn(t)), bounds=(float(lower), float(upper)), method="bounded")
        if not res.success:
            return None
        return float(res.x), float(res.fun)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# regression / correlation
# ---------------------------------------------------------------------------

def polyfit(xs: Sequence[float], ys: Sequence[float], degree: int = 1) -> dict | None:
    """Least-squares polynomial fit. Returns ``{coeffs, r2}`` (coeffs high->low)."""
    if not _HAS_NUMPY or len(xs) != len(ys) or len(xs) < degree + 1:
        return None
    try:
        x = np.asarray(list(xs), dtype=float)
        y = np.asarray(list(ys), dtype=float)
        coeffs = np.polyfit(x, y, degree)
        p = np.poly1d(coeffs)
        yhat = p(x)
        ss_res = float(np.sum((y - yhat) ** 2))
        ss_tot = float(np.sum((y - float(np.mean(y))) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-15 else 1.0
        return {"coeffs": [float(c) for c in coeffs], "r2": r2}
    except Exception:  # noqa: BLE001
        return None


def correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation coefficient (pure Python, no numpy requirement)."""
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    try:
        x = [float(v) for v in xs]
        y = [float(v) for v in ys]
        mx = sum(x) / n
        my = sum(y) / n
        sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
        sxx = sum((a - mx) ** 2 for a in x)
        syy = sum((b - my) ** 2 for b in y)
        if sxx <= 0 or syy <= 0:
            return None
        return sxy / math.sqrt(sxx * syy)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# probability distributions (scipy.stats)
# ---------------------------------------------------------------------------

def normal_cdf(x: float, mean: float = 0.0, sd: float = 1.0) -> float | None:
    if not _HAS_SCIPY or sd <= 0:
        return None
    try:
        return float(_stats.norm.cdf(float(x), loc=float(mean), scale=float(sd)))
    except Exception:  # noqa: BLE001
        return None


def normal_pdf(x: float, mean: float = 0.0, sd: float = 1.0) -> float | None:
    if not _HAS_SCIPY or sd <= 0:
        return None
    try:
        return float(_stats.norm.pdf(float(x), loc=float(mean), scale=float(sd)))
    except Exception:  # noqa: BLE001
        return None


def binomial_pmf(k: int, n: int, p: float) -> float | None:
    if not _HAS_SCIPY or not (0 <= p <= 1) or n < 0:
        return None
    try:
        return float(_stats.binom.pmf(int(k), int(n), float(p)))
    except Exception:  # noqa: BLE001
        return None


def binomial_cdf(k: int, n: int, p: float) -> float | None:
    if not _HAS_SCIPY or not (0 <= p <= 1) or n < 0:
        return None
    try:
        return float(_stats.binom.cdf(int(k), int(n), float(p)))
    except Exception:  # noqa: BLE001
        return None


def poisson_pmf(k: int, lam: float) -> float | None:
    if not _HAS_SCIPY or lam < 0:
        return None
    try:
        return float(_stats.poisson.pmf(int(k), float(lam)))
    except Exception:  # noqa: BLE001
        return None


def poisson_cdf(k: int, lam: float) -> float | None:
    if not _HAS_SCIPY or lam < 0:
        return None
    try:
        return float(_stats.poisson.cdf(int(k), float(lam)))
    except Exception:  # noqa: BLE001
        return None
