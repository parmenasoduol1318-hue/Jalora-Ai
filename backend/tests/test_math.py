"""Mathematical calculation engine tests (spec §math, items 1-11).

Covers: arithmetic, fractions, algebra/equations, calculus, trigonometry,
statistics/probability, unit conversions, geometry, word problems, ratios,
percentages, arithmetic word problems, safe-failure handling, the intent
classifier's direct truth table, the calculator / verify tools, and the
chat fast-path (spec #9: simple sums must NOT call the model).

Run from ``jalora/backend`` (venv python)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the backend importable both when run under the backend cwd and from
# the repo root (pytest rootdir is the backend tests/ package).
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import math_engine as me  # noqa: E402
from app.tools import math_tools  # noqa: E402
from app.tools import registry  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _solve(text: str):
    return me.solve(text)


def _exact(text: str) -> str:
    return _solve(text).exact


# --------------------------------------------------------------------------- #
# (1) SymPy symbolic arithmetic + fractions
# --------------------------------------------------------------------------- #
def test_operator_precedence():
    assert _exact("2+2*5") == "12"
    assert _exact("3+4*2") == "11"


def test_exact_fractions():
    assert _exact("1/2+1/3") == "5/6"
    assert _exact("2/3+1/4") == "11/12"


def test_powers_and_roots():
    assert _exact("2^10") == "1024"
    assert _exact("sqrt(16)") == "4"
    res = _solve("2^0.5")
    assert res.ok
    assert "1.41421356237310" in res.exact + res.numeric


# --------------------------------------------------------------------------- #
# (2) Algebra / equations  (+ substitution verification)
# --------------------------------------------------------------------------- #
def test_quadratic_solutions():
    res = _solve("x^2-5x+6=0")
    assert res.ok
    assert "x = 2" in res.exact and "x = 3" in res.exact
    # both roots verified by substitution (spec #7)
    assert me.verify_math("2", "x^2-5x+6=0").verified
    assert me.verify_math("3", "x^2-5x+6=0").verified
    assert not me.verify_math("0", "x^2-5x+6=0").verified


def test_linear_equations():
    assert _exact("x+1=4") == "x = 3"
    assert _exact("2x+3=7") == "x = 2"
    assert _exact("3x-5=10") == "x = 5"
    assert me.verify_math("3", "x+1=4").verified
    assert not me.verify_math("2", "x+1=4").verified


# --------------------------------------------------------------------------- #
# (1) Calculus: differentiate / integrate / limits
# --------------------------------------------------------------------------- #
def test_differentiation():
    assert _exact("differentiate x^3") == "3*x**2"
    res = _solve("differentiate sin(x)*x")
    assert res.ok
    assert "cos" in res.exact and "sin" in res.exact


def test_integration():
    assert _exact("integrate 3x^2") == "x**3"


def test_limits():
    assert _exact("limit sin(x)/x as x->0") == "1"


# --------------------------------------------------------------------------- #
# (2) Trigonometry
# --------------------------------------------------------------------------- #
def test_trig_values():
    assert _exact("sin(pi/2)") == "1"
    assert _exact("cos(0)") == "1"
    assert _exact("tan(pi/4)") == "1"


# --------------------------------------------------------------------------- #
# (2) Logarithms
# --------------------------------------------------------------------------- #
def test_logarithms():
    res = _solve("log(100)")
    assert res.ok and res.exact == "log(100)"


# --------------------------------------------------------------------------- #
# (2) Unit conversions
# --------------------------------------------------------------------------- #
def test_unit_conversions():
    res = _solve("5 km to m")
    assert res.ok and "5000" in res.exact and "m" in (res.units or "")
    # the formatted reply carries the unit
    assert "5000" in me.format_reply(res) and "m" in me.format_reply(res)


def test_statistics():
    assert _exact("mean of 1 2 3 4 5") == "3"
    assert _exact("nCr(5,2)") == "10"
    assert _exact("nPr(5,2)") == "20"


def test_geometry():
    res = _solve("area of circle r=5")
    assert res.ok and "78.539816" in res.exact
    res = _solve("circumference of circle r=5")
    assert res.ok and "31.415927" in res.exact


# --------------------------------------------------------------------------- #
# (4) Mathematical word problems
# --------------------------------------------------------------------------- #
def test_speed_word_problem():
    res = _solve("If a car travels 150 km in 3 hours, what is its speed?")
    assert res.ok and "50" in res.exact
    # reply carries the unit
    assert "50" in me.format_reply(res) and "km/h" in me.format_reply(res)


def test_percentage_word_problem():
    assert _exact("what is 20% of 150") == "30"
    assert _exact("25% of 80") == "20"


def test_arithmetic_word_problems():
    assert _exact("add 3 and 5") == "8"
    assert _exact("3 plus 5") == "8"
    assert _exact("6 times 7") == "42"
    assert _exact("10 minus 4") == "6"
    assert _exact("divide 84 by 7") == "12"


def test_ratios():
    assert _exact("ratio of 3 to 2") == "1.5"
    assert _exact("ratio of 4 to 5") == "0.8"


# --------------------------------------------------------------------------- #
# (5) Step-by-step replies are formatted with math + highlighted answer
# --------------------------------------------------------------------------- #
def test_format_reply_contents():
    reply = me.format_reply(me.solve("2+2*5"))
    assert "> **Answer:**" in reply          # highlighted final answer block
    assert "12" in reply
    assert "verified" in reply               # (7) verification marker
    # LaTeX math delimiters are present for MathJax/KaTeX rendering
    assert ("\\(" in reply) or ("$$" in reply)


# --------------------------------------------------------------------------- #
# (8) Safe handling of invalid / unsupported input — never invent
# --------------------------------------------------------------------------- #
def test_division_by_zero_is_safe():
    res = _solve("1/0")
    assert not res.ok
    assert "Division by zero" in (res.error or "")


def test_incomplete_expression_is_safe():
    res = _solve("2+")
    assert not res.ok and "couldn't parse" in (res.error or "").lower()


def test_prose_statement_not_invented():
    # Lowercase 'e' is a free symbol here (not Euler's number) -> no number ->
    # must NOT be forced into a (symbolic) answer.
    res = _solve("log(e)")
    assert not res.ok


# --------------------------------------------------------------------------- #
# (3) Intent classifier: direct truth table + prose is NOT math
# --------------------------------------------------------------------------- #
DIRECT_MATH = [
    "2+2*5", "1/2+1/3", "differentiate x^3", "integrate x^2",
    "limit sin(x)/x as x->0", "sin(pi/2)", "log(100)", "5 km to m",
    "mean of 1 2 3 4 5", "nCr(5,2)", "add 3 and 5", "3 plus 5",
    "6 times 7", "what is 20% of 150", "x^2-5x+6=0",
]
PROSE_NOT_MATH = [
    "I have 3 cats", "tell me a joke", "meeting at 3pm",
    "what is the weather", "the cost is 3 dollars", "I live 5 miles",
    "I scored 3 goals in the first half", "add salt to taste",
    "log into my bank account", "check the blog settings",
    "Text +254700000001 saying hi there",
    "Call +254700000101 and ask about my application",
    "The contract phone is +12025550123, please call it",
]


@pytest.mark.parametrize("text", DIRECT_MATH)
def test_classify_direct_math(text):
    sig = me.classify_math(text)
    assert sig is not None, f"expected math signal for {text!r}"
    assert sig.direct is True


@pytest.mark.parametrize("text", [
    "If a car travels 150 km in 3 hours, what is its speed?",
    "ratio of 3 to 2",
])
def test_classify_word_problem_is_not_direct(text):
    sig = me.classify_math(text)
    assert sig is not None and sig.intent == "word_problem" and sig.direct is False


@pytest.mark.parametrize("text", PROSE_NOT_MATH)
def test_classify_prose_is_not_math(text):
    assert me.classify_math(text) is None


# --------------------------------------------------------------------------- #
# verify_math tool (spec #7) returns a MathResult
# --------------------------------------------------------------------------- #
def test_verify_math_tool():
    assert me.verify_math("2", "x^2-5x+6=0").verified
    assert not me.verify_math("0", "x^2-5x+6=0").verified
    assert me.verify_math("3", "x+1=4").verified


# --------------------------------------------------------------------------- #
# Tool registration: calculate + verify_math are AUTO math tools
# --------------------------------------------------------------------------- #
def test_calculate_and_verify_registered():
    assert registry.get("calculate") is not None
    assert registry.get("verify_math") is not None
    assert registry.get("calculate").permission is not None  # AUTO


def test_calculate_tool_runs_pure_math():
    tr = math_tools.calculate("2+2*5")
    assert tr.success is True
    assert "12" in tr.message
    tr2 = math_tools.calculate("1/0")
    assert tr2.success is False
    assert "Division by zero" in (tr2.message or "")


def test_verify_math_tool_runs():
    tr = math_tools.verify_math("2", "x^2-5x+6=0")
    assert tr.success is True
    assert tr.data["verified"] is True
    tr2 = math_tools.verify_math("0", "x^2-5x+6=0")
    assert tr2.success is True
    assert tr2.data["verified"] is False


# --------------------------------------------------------------------------- #
# (9) Fast-path integration: simple sums compute WITHOUT the model
# --------------------------------------------------------------------------- #
def _auth_headers(client, email="math-fast@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Math",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_fast_path_computes_then_explains(client, fake_router):
    """A pure arithmetic question is computed by SymPy (authoritative) and, when
    a model is available, the model adds a step-by-step explanation around the
    result — it never replaces the computed answer (spec #6, Phase 0)."""
    from langchain_core.messages import AIMessage

    fake_router.script.append(AIMessage(
        content="Multiply the 2 and 5 first (order of operations), then add the "
                "remaining 2. That work is shown below the explanation."
    ))
    headers = _auth_headers(client, "math-fast@test.com")
    resp = client.post("/api/chat", json={"message": "2+2*5"}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["status"] == "completed"
    assert data["model"] == "sympy"            # computed, not a language model
    assert data["intent"] == "math"
    assert "12" in data["reply"]
    assert "**Answer:**" in data["reply"]      # verified answer block appended
    # the model was consulted only to *explain*, and its prose is included
    assert len(fake_router.tape) == 1
    assert "Multiply the 2 and 5" in data["reply"]


def test_fast_path_explain_disabled_never_calls_model(client, fake_router, monkeypatch):
    """With explanations turned off, a pure sum is answered by SymPy alone and
    the model is never invoked (spec #9: works when no model is available)."""
    from app.config import get_settings

    monkeypatch.setenv("JALORA_MATH_EXPLAIN", "false")
    get_settings.cache_clear()
    try:
        headers = _auth_headers(client, "math-noexplain@test.com")
        resp = client.post("/api/chat", json={"message": "2+2*5"}, headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "12" in data["reply"]
        assert len(fake_router.tape) == 0
        assert len(fake_router.script) == 0
    finally:
        get_settings.cache_clear()


def test_fast_path_safe_failure_returns_error(client, fake_router):
    """Unsupported/failed math still bypasses the model and reports a structured
    error instead of hallucinating."""
    headers = _auth_headers(client, "math-err@test.com")
    resp = client.post("/api/chat", json={"message": "1/0"}, headers=headers)
    assert resp.status_code == 502
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "MATH_ERROR"
    assert "Division by zero" in body["error"]["details"]
    assert len(fake_router.tape) == 0


def test_prose_does_not_hit_fast_path(client, fake_router):
    """Plain conversation must still flow through the model (the fast-path must
    not swallow non-math turns)."""
    from langchain_core.messages import AIMessage

    fake_router.script.append(AIMessage(content="Hi there!"))
    headers = _auth_headers(client, "math-prose@test.com")
    resp = client.post("/api/chat", json={"message": "Hello"}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "Hi there" in data["reply"]
    # the model WAS consulted for a normal question
    assert len(fake_router.tape) >= 1
    assert data["model"] != "sympy"


# --------------------------------------------------------------------------- #
# Regression: calculus-verb typo tolerance + equation routing (spec math §robust)
# --------------------------------------------------------------------------- #
def test_calculus_verb_typo_routes_to_sympy():
    # "intergrate" (transposed r/g) must still classify as calculus and be
    # computed by SymPy (sp.integrate), not mis-routed to the arithmetic path
    # that produced "couldn't parse 'intergrate 3x^2' as a numeric expression".
    sig = me.classify_math("intergrate 3x^2")
    assert sig is not None and sig.intent == "calculus"
    res = me.solve("intergrate 3x^2")
    assert res.ok and res.exact == "x**3"
    assert "x^{3}" in me.format_reply(res)


def test_linear_equation_is_solved_not_summed():
    # "2 - 5x + 6 = 0" is an equation (solution x = 8/5); it must NOT degrade to
    # the arithmetic last-resort that sums every number to 13.
    res = me.solve("2 - 5x + 6 = 0")
    assert res.ok and res.intent == "algebra"
    assert res.exact == "x = 8/5"
    reply = me.format_reply(res)
    assert "8/5" in reply
    assert "13" not in reply


# --------------------------------------------------------------------------- #
# Phase 0: definite integrals, systems, prose guard, friendly errors (spec #8)
# --------------------------------------------------------------------------- #
def test_definite_integral_closed_form():
    # Regression: "integrate sin(x) dx from 0 to pi" used to fail with
    # "Integration failed: invalid syntax (<string>, line 1)".
    res = me.solve("integrate sin(x) dx from 0 to pi")
    assert res.ok, res.error
    assert res.exact == "2"
    assert "\\int_" in me.format_reply(res)


def test_definite_integral_polynomial():
    res = me.solve("integrate x^2 from 0 to 3")
    assert res.ok, res.error
    assert res.exact == "9"


def test_definite_integral_special_function_is_numeric():
    # No elementary antiderivative here -> exact retains erf but a decimal is given.
    res = me.solve("integrate exp(-x^2) from 0 to 1")
    assert res.ok, res.error
    assert res.numeric
    assert 0.74 < float(res.numeric) < 0.75


def test_integrate_prose_does_not_fabricate():
    # "integrate the velocity" used to fabricate c*e*i*l*o*t*v*x*y.
    res = me.solve("integrate the velocity")
    assert not res.ok
    assert "c*e" not in (res.exact or "")
    reply = me.format_reply(res)
    assert "<string>" not in reply and "line 1" not in reply


def test_system_of_equations_solved():
    res = me.solve("2x + 3y = 6 and x - y = 1")
    assert res.ok, res.error
    assert "x = 9/5" in res.exact and "y = 4/5" in res.exact
    assert res.verified


def test_prose_science_is_not_direct():
    # Narrative physics must NOT be fast-pathed — it flows to the agent.
    sig = me.classify_math(
        "calculate the kinetic energy of a 2 kg mass moving at 3 m/s"
    )
    assert sig is None or sig.direct is False


def test_bare_expression_is_direct():
    sig = me.classify_math("2+2*5")
    assert sig is not None and sig.direct is True


# --------------------------------------------------------------------------- #
# Phase 1: real unit algebra (pint) — compound/derived units + dimensions
# --------------------------------------------------------------------------- #
def test_compound_unit_conversion():
    res = _solve("90 km/h to m/s")
    assert res.ok, res.error
    assert res.exact == "25"
    assert "m/s" in (res.units or "")


def test_derived_unit_equivalence():
    res = _solve("5 kg*m/s^2 to N")
    assert res.ok, res.error
    assert res.exact == "5"


def test_temperature_conversion():
    res = _solve("100 celsius to fahrenheit")
    assert res.ok, res.error
    assert res.exact == "212"


def test_incompatible_units_rejected():
    res = _solve("5 km to kg")
    assert not res.ok
    assert "incompatible" in (res.error or "").lower()


# --------------------------------------------------------------------------- #
# Phase 2: broadened mathematics — linear algebra, number theory, series,
# ODEs, complex numbers, probability distributions, regression (SciPy engine)
# --------------------------------------------------------------------------- #
from app.services import math_numeric as mn  # noqa: E402


def test_numeric_engine_available():
    # SciPy/NumPy power the numeric Phase 2 handlers.
    assert mn.has_numpy() is True
    assert mn.has_scipy() is True
    assert mn.quad(lambda t: t * t, 0, 3) == pytest.approx(9.0, rel=1e-9)


# --- linear algebra -------------------------------------------------------- #
def test_matrix_determinant():
    res = _solve("determinant of [[1,2],[3,4]]")
    assert res.ok, res.error
    assert res.exact == "-2"
    assert res.intent == "linalg"


def test_matrix_inverse():
    res = _solve("inverse of [[1,2],[3,4]]")
    assert res.ok, res.error
    assert res.exact == "[-2, 1; 3/2, -1/2]"


def test_matrix_singular_inverse_rejected():
    res = _solve("inverse of [[1,2],[2,4]]")
    assert not res.ok
    assert "singular" in (res.error or "").lower()


def test_matrix_eigenvalues():
    res = _solve("eigenvalues of [[2,0],[0,3]]")
    assert res.ok, res.error
    assert "2" in res.exact and "3" in res.exact


def test_matrix_transpose_and_rank():
    res = _solve("transpose of [[1,2],[3,4]]")
    assert res.ok and res.exact == "[1, 3; 2, 4]"
    res = _solve("rank of [[1,2],[2,4]]")
    assert res.ok and res.exact == "1"


def test_matrix_multiplication_and_dot_product():
    res = _solve("[[1,2],[3,4]] * [[5,6],[7,8]]")
    assert res.ok, res.error
    assert res.exact == "[19, 22; 43, 50]"
    res = _solve("dot product of [1,2,3] and [4,5,6]")
    assert res.ok and res.exact == "32"


# --- number theory --------------------------------------------------------- #
def test_gcd_lcm():
    assert _solve("gcd(12, 18)").exact == "6"
    assert _solve("gcd of 12 and 18").exact == "6"
    assert _solve("lcm of 4 and 6").exact == "12"
    assert _solve("gcd(12, 18)").intent == "number_theory"


def test_prime_factorisation():
    res = _solve("prime factorization of 360")
    assert res.ok, res.error
    assert res.exact == "2^3 \u00d7 3^2 \u00d7 5"


def test_divisors():
    assert _solve("divisors of 28").exact == "1, 2, 4, 7, 14, 28"
    assert _solve("factors of 28").exact == "1, 2, 4, 7, 14, 28"


def test_primality():
    assert _solve("is 97 prime").exact == "prime"
    assert _solve("is 98 prime").exact == "not prime"


def test_totient_mod_and_inverse():
    assert _solve("phi(10)").exact == "4"
    assert _solve("euler phi of 10").exact == "4"
    assert _solve("17 mod 5").exact == "2"
    assert _solve("inverse of 3 mod 11").exact == "4"


def test_fibonacci_and_factorial():
    assert _solve("fibonacci(10)").exact == "55"
    assert _solve("factorial(5)").exact == "120"


# --- series ---------------------------------------------------------------- #
def test_summation_infinite_series():
    res = _solve("sum of 1/n^2 from n=1 to infinity")
    assert res.ok, res.error
    assert res.exact == "pi**2/6"
    assert float(res.numeric) == pytest.approx(1.6449340668, rel=1e-6)


def test_summation_geometric_series():
    assert _solve("sum of 1/2^n from n=0 to infinity").exact == "2"


def test_taylor_series():
    res = _solve("taylor series of sin(x) around 0 up to x^5")
    assert res.ok, res.error
    assert res.exact == "x**5/120 - x**3/6 + x"
    assert res.intent == "series"


# --- ordinary differential equations -------------------------------------- #
def test_ode_symbolic():
    res = _solve("solve the differential equation y' = y")
    assert res.ok, res.error
    assert res.exact == "y = C1*exp(x)"
    assert res.intent == "ode"


def test_ode_second_order_symbolic():
    res = _solve("solve y'' + y = 0")
    assert res.ok, res.error
    assert "sin" in res.exact and "cos" in res.exact


def test_ode_numeric_ivp():
    res = _solve("solve y' = -2y with y(0)=1 from 0 to 5")
    assert res.ok, res.error
    # y(5) = e^{-10} ~ 4.54e-5
    assert float(res.numeric) == pytest.approx(0.0000454, rel=1e-2)


# --- complex numbers ------------------------------------------------------- #
def test_complex_modulus_conjugate_argument():
    assert _solve("modulus of 3+4i").exact == "5"
    assert _solve("conjugate of 3+4i").exact == "3 - 4i"
    assert _solve("argument of 1+i").exact == "pi/4"


def test_complex_arithmetic_and_parts():
    assert _solve("(1+i)^2").exact == "2i"
    assert _solve("real part of 3+4i").exact == "3"
    assert _solve("imaginary part of 3+4i").exact == "4"


# --- probability distributions --------------------------------------------- #
def test_normal_distribution_cdf():
    res = _solve("P(X < 1.96) for a standard normal distribution")
    assert res.ok, res.error
    assert float(res.exact) == pytest.approx(0.975002, abs=1e-6)


def test_binomial_distribution_pmf():
    res = _solve("binomial distribution n=10 p=0.5, P(X = 3)")
    assert res.ok, res.error
    assert res.exact.startswith("0.117")


def test_poisson_distribution_pmf():
    res = _solve("poisson distribution lambda=2, P(X = 3)")
    assert res.ok, res.error
    assert res.exact.startswith("0.180")


# --- regression / correlation ---------------------------------------------- #
def test_line_of_best_fit():
    res = _solve("line of best fit for (1,2),(2,4),(3,6),(4,8)")
    assert res.ok, res.error
    assert res.exact == "y = 2*x"
    assert res.intent == "regression"


def test_correlation_coefficient():
    res = _solve("correlation of (1,2),(2,4),(3,6)")
    assert res.ok, res.error
    assert res.exact == "r = 1"


# --- classifier cues for Phase 2 ------------------------------------------- #
@pytest.mark.parametrize("text,intent", [
    ("determinant of [[1,2],[3,4]]", "linalg"),
    ("gcd(12, 18)", "number_theory"),
    ("taylor series of sin(x)", "series"),
    ("solve the differential equation y' = y", "ode"),
    ("modulus of 3+4i", "complex"),
    ("binomial distribution n=10 p=0.5", "distribution"),
    ("line of best fit for (1,2),(2,4)", "regression"),
])
def test_classify_phase2_intents(text, intent):
    sig = me.classify_math(text)
    assert sig is not None, f"expected math signal for {text!r}"
    assert sig.intent == intent


@pytest.mark.parametrize("text", [
    "who is the prime minister",
    "prime minister of Kenya",
    "the matrix is a good movie",
    "normal day at work",
    "a complex situation at the office",
])
def test_phase2_cues_do_not_route_prose(text):
    assert me.classify_math(text) is None


# --------------------------------------------------------------------------- #
# Phase 3: physics engine — constants table + dimensional formula solver (pint)
# --------------------------------------------------------------------------- #
from app.services import physics_engine as pe  # noqa: E402


def test_physics_engine_available():
    assert pe.is_available() is True
    assert len(pe.all_constants()) >= 20


def test_physics_constants_table():
    c = pe.constant("speed of light")
    assert c is not None and c.symbol == "c"
    planck = pe.constant("Planck's constant")
    assert planck is not None and planck.symbol == "h"
    gas = pe.constant("gas constant")
    assert gas is not None and gas.symbol == "R"
    electron = pe.constant("mass of the electron")
    assert electron is not None and electron.symbol == "m_e"


def test_physics_constant_lookup_as_result():
    res = _solve("what is the speed of light")
    assert res.ok and res.intent == "physics"
    assert res.exact == "299792458"
    assert res.units == "m/s"


def test_physics_constant_prose_guard():
    # "gravity" is a weak alias: only in an explicit query frame, never in prose.
    assert pe.solve("the gravity of the situation") is None
    assert pe.solve("gravity of the market") is None
    res = _solve("what is gravity")
    assert res.ok and res.exact == "9.80665"


def test_physics_kinetic_energy():
    res = _solve("kinetic energy of a 2 kg object moving at 3 m/s")
    assert res.ok and res.intent == "physics" and res.verified
    assert res.exact == "9" and res.units == "J"


def test_physics_potential_energy():
    res = _solve("gravitational potential energy of a 5 kg object at height 10 m")
    assert res.ok, res.error
    assert float(res.exact) == pytest.approx(490.3325, rel=1e-4)


def test_physics_weight_and_force_of_gravity():
    res = _solve("weight of a 70 kg person")
    assert res.ok and float(res.exact) == pytest.approx(686.4655, rel=1e-4)
    assert _solve("the force of gravity on a 70 kg person").units == "N"


def test_physics_work_and_power():
    assert _solve("work done by a force of 10 N over 5 m").exact == "50"
    res = _solve("power when 100 J of work is done in 4 s")
    assert res.ok and res.exact == "25" and res.units == "W"


def test_physics_momentum_and_density():
    mom = _solve("momentum of a 2 kg object at 4 m/s")
    assert mom.ok and mom.exact == "8" and mom.units == "kg\u00b7m/s"
    den = _solve("density of a mass of 500 g occupying 250 cm^3")
    assert den.ok and float(den.exact) == pytest.approx(2000.0, rel=1e-6)


def test_physics_pressure_and_second_law():
    assert _solve("pressure of a force of 100 N over 2 m^2").exact == "50"
    res = _solve("force on a 10 kg mass accelerating at 2 m/s^2")
    assert res.ok and res.exact == "20" and res.units == "N"


def test_physics_acceleration():
    res = _solve("a car accelerates from 0 m/s to 20 m/s in 4 s")
    assert res.ok and res.exact == "5" and res.units == "m/s\u00b2"


def test_physics_waves():
    assert float(_solve("wavelength of a wave with speed 340 m/s and frequency 500 Hz").exact) == pytest.approx(0.68)
    assert float(_solve("frequency of a wave with wavelength 0.5 m and speed 340 m/s").exact) == pytest.approx(680.0)


def test_physics_ohms_law_solves_for_current():
    res = _solve("voltage 12 V and resistance 3 ohm")
    assert res.ok and res.exact == "4" and res.units == "A"


def test_physics_gravitational_and_coulomb_force():
    g = _solve("gravitational force between 5 kg and 3 kg separated by 2 m")
    assert g.ok and g.units == "N"
    assert float(g.exact) == pytest.approx(2.5028625e-10, rel=1e-4)
    c = _solve("coulomb force between 1 C and 1 C at 1 m")
    assert c.ok and float(c.exact) == pytest.approx(8.9875517923e9, rel=1e-4)


def test_physics_ideal_gas_law():
    res = _solve("an ideal gas at 300 K and 100 kPa occupies 0.05 m^3, find the number of moles")
    assert res.ok and res.units == "mol"
    assert float(res.exact) == pytest.approx(2.00454, rel=1e-3)


def test_physics_does_not_hijack_plain_math():
    assert _solve("2+2").intent == "arithmetic"
    assert _solve("convert 5 km to m").intent == "units"
    assert pe.solve("solve x^2-5x+6=0") is None
    assert pe.solve("hello there, how are you") is None


# --------------------------------------------------------------------------- #
# Phase 4: chemistry engine — periodic table + formula/molar-mass/balancing
# --------------------------------------------------------------------------- #
from app.services import chemistry_engine as ce  # noqa: E402


def test_chemistry_engine_available():
    assert ce.is_available() is True
    assert len(ce.all_elements()) == 118


def test_chemistry_element_lookup():
    assert ce.element("Fe").z == 26
    assert ce.element("fe").symbol == "Fe"
    assert ce.element("iron").name == "Iron"
    assert ce.element("aluminum").symbol == "Al"          # common alias
    assert ce.element("26").symbol == "Fe"                # atomic number
    assert ce.element("gold").mass == 196.966569


def test_chemistry_parse_formula():
    assert ce.parse_formula("H2O") == {"H": 2, "O": 1}
    assert ce.parse_formula("Ca(OH)2") == {"Ca": 1, "O": 2, "H": 2}
    assert ce.parse_formula("C6H12O6") == {"C": 6, "H": 12, "O": 6}
    assert ce.parse_formula("H\u2082O") == {"H": 2, "O": 1}  # unicode subscripts
    assert ce.parse_formula("Xx2") is None               # unknown element
    assert ce.parse_formula("H2(O") is None              # unbalanced group


def test_chemistry_molar_mass():
    assert ce.molar_mass("H2O") == pytest.approx(18.015, rel=1e-6)
    assert ce.molar_mass("C6H12O6") == pytest.approx(180.156, rel=1e-6)
    assert ce.molar_mass("Ca(OH)2") == pytest.approx(74.092, rel=1e-6)
    hydrate = ce.molar_mass("CuSO4\u00b75H2O")
    assert hydrate == pytest.approx(249.677, rel=1e-5)
    assert ce.molar_mass("Zzz") is None


def test_chemistry_molar_mass_result():
    res = _solve("molar mass of water")
    assert res.ok and res.intent == "chemistry" and res.verified
    assert res.exact == "18.015" and res.units == "g/mol"
    assert _solve("molar mass of glucose").exact == "180.156"


def test_chemistry_element_results():
    assert _solve("atomic number of iron").exact == "26"
    mass = _solve("atomic mass of Au")
    assert mass.ok and mass.units == "u" and mass.exact == "196.967"
    assert _solve("symbol for sodium").exact == "Na"
    assert _solve("what element is Fe").exact == "Iron"
    assert _solve("element 26").exact == "Iron"


def test_chemistry_balance_equation():
    assert _solve("balance H2 + O2 = H2O").exact == "2 H2 + O2 -> 2 H2O"
    eq = _solve("balance the equation Fe + O2 = Fe2O3")
    assert eq.ok and eq.exact == "4 Fe + 3 O2 -> 2 Fe2O3"
    assert _solve("balance CH4 + O2 = CO2 + H2O").exact == "CH4 + 2 O2 -> CO2 + 2 H2O"


def test_chemistry_balance_refuses_unbalanceable():
    assert ce.balance_equation("H2 + O2 = H2O") == (["H2", "O2"], ["H2O"], [2, 1, 2])
    assert ce.balance_equation("H2O = CO2") is None      # element set mismatch
    assert ce.balance_equation("not an equation") is None


def test_chemistry_mole_mass_conversions():
    mol = _solve("how many moles in 100 g of water")
    assert mol.ok and mol.units == "mol"
    assert float(mol.exact) == pytest.approx(5.55093, rel=1e-4)
    grams = _solve("mass of 2 mol of NaCl")
    assert grams.ok and grams.units == "g" and grams.exact == "116.88"


def test_chemistry_does_not_hijack_plain_math():
    assert ce.solve("2+2") is None
    assert ce.solve("solve x^2-5x+6=0") is None
    assert ce.solve("what is the speed of light") is None
    assert ce.solve("hello there") is None
    assert _solve("2+2").intent == "arithmetic"


# --------------------------------------------------------------------------- #
# Phase 5: pedagogical levels + deterministic (offline) derivations
# --------------------------------------------------------------------------- #
from app.services import pedagogy  # noqa: E402


def test_pedagogy_normalize_level():
    assert pedagogy.normalize_level("primary") == "primary"
    assert pedagogy.normalize_level("High-School") == "highschool"
    assert pedagogy.normalize_level("post-graduate") == "postgrad"
    assert pedagogy.normalize_level("kid") == "primary"          # alias
    assert pedagogy.normalize_level("undergrad") == "stem"       # alias
    assert pedagogy.normalize_level("PhD") == "postgrad"         # alias
    assert pedagogy.normalize_level("auto") == "auto"
    assert pedagogy.normalize_level("") == "auto"
    assert pedagogy.normalize_level(None) == "auto"
    assert pedagogy.normalize_level("nonsense") == "auto"        # unknown => auto


def test_pedagogy_infer_level():
    assert pedagogy.infer_level("explain like I'm 5") == "primary"
    assert pedagogy.infer_level("in simple words please") == "primary"
    assert pedagogy.infer_level("this is for a high school student") == "highschool"
    assert pedagogy.infer_level("explain for a university student") == "university"
    assert pedagogy.infer_level("be rigorous about this") == "professor"
    assert pedagogy.infer_level("for a professor, cite the theorem") == "professor"
    assert pedagogy.infer_level("for a postgraduate, note edge cases") == "postgrad"
    assert pedagogy.infer_level("what is 2+2") == "auto"
    assert pedagogy.infer_level(None) == "auto"


def test_pedagogy_resolve_level():
    # explicit config wins over the question
    assert pedagogy.resolve_level("primary", "for a professor") == "primary"
    # else infer from the question
    assert pedagogy.resolve_level("auto", "for a university student") == "university"
    assert pedagogy.resolve_level(None, "explain like I'm 5") == "primary"
    # nothing to go on => auto
    assert pedagogy.resolve_level("auto", "what is 2+2") == "auto"


def test_pedagogy_model_directive():
    assert "primary-school" in pedagogy.model_directive("primary")
    assert "STEM undergraduate" in pedagogy.model_directive("stem")
    assert "level suitable" in pedagogy.model_directive("auto")
    # aliases resolve before lookup
    assert pedagogy.model_directive("undergrad") == pedagogy.model_directive("stem")


def test_pedagogy_render_level_aware():
    res = _solve("2+3")
    primary = pedagogy.render(res, "primary")
    assert primary.startswith(pedagogy.LEVELS["primary"].header)
    assert "**Answer:**" in primary          # verified block always preserved
    assert "5" in primary
    # professor has no header, so it is exactly the plain verified block
    assert pedagogy.render(res, "professor") == me.format_reply(res)
    # auto (default) is unchanged from format_reply
    assert pedagogy.render(res, "auto") == me.format_reply(res)
    assert pedagogy.render(res) == me.format_reply(res)


def test_pedagogy_render_infers_from_question():
    res = _solve("2+3")
    rendered = pedagogy.render(res, "auto", question="please explain like I'm 5")
    assert rendered.startswith(pedagogy.LEVELS["primary"].header)


def test_pedagogy_render_preserves_failure_block():
    bad = me.MathResult(ok=False, error="No expression provided.")
    # an error/incomplete result is never given a level header
    assert pedagogy.render(bad, "primary") == me.format_reply(bad)


def test_pedagogy_explain_math_offline_level_aware(monkeypatch):
    """With the model explanation disabled (offline), the level still shapes the
    deterministic working — proof that 'show the working' adapts without a model."""
    import asyncio

    from app.config import get_settings
    from app.services import science_explain

    monkeypatch.setenv("JALORA_MATH_EXPLAIN", "false")
    monkeypatch.setenv("JALORA_MATH_LEVEL", "primary")
    get_settings.cache_clear()
    try:
        out = asyncio.run(science_explain.explain_math(
            router=None, question="2+3", result=_solve("2+3")))
        assert out.startswith(pedagogy.LEVELS["primary"].header)
        assert "**Answer:**" in out and "5" in out
    finally:
        get_settings.cache_clear()


def test_fast_path_offline_respects_configured_level(client, monkeypatch):
    """End-to-end: JALORA_MATH_LEVEL shapes the offline answer for a bare sum
    (the model is never consulted)."""
    from app.config import get_settings

    monkeypatch.setenv("JALORA_MATH_EXPLAIN", "false")
    monkeypatch.setenv("JALORA_MATH_LEVEL", "highschool")
    get_settings.cache_clear()
    try:
        headers = _auth_headers(client, "math-level@test.com")
        resp = client.post("/api/chat", json={"message": "2+2*5"}, headers=headers)
        assert resp.status_code == 200, resp.text
        reply = resp.json()["reply"]
        assert reply.startswith(pedagogy.LEVELS["highschool"].header)
        assert "12" in reply and "**Answer:**" in reply
    finally:
        get_settings.cache_clear()


def test_fast_path_offline_auto_level_has_no_header(client, monkeypatch):
    """Default (auto) level leaves the offline block exactly as before."""
    from app.config import get_settings

    monkeypatch.setenv("JALORA_MATH_EXPLAIN", "false")
    monkeypatch.setenv("JALORA_MATH_LEVEL", "auto")
    get_settings.cache_clear()
    try:
        headers = _auth_headers(client, "math-level-auto@test.com")
        resp = client.post("/api/chat", json={"message": "2+2*5"}, headers=headers)
        assert resp.status_code == 200, resp.text
        reply = resp.json()["reply"]
        assert not reply.startswith("**")           # no level header
        assert "12" in reply and "**Answer:**" in reply
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Phase 7: science intent, science brief, and the "show working" toggle
# --------------------------------------------------------------------------- #
from app.agents import intent as _intent  # noqa: E402
from app.agents import prompts as _prompts  # noqa: E402
from app.tools import routing as _routing  # noqa: E402


@pytest.mark.parametrize("text", [
    "what is the kinetic energy of a 2 kg mass at 3 m/s",
    "balance the equation Fe + O2 = Fe2O3",
    "calculate the molar mass of H2O",
    "explain why a heavier object has more gravitational potential energy",
    "what is the acceleration due to gravity",
    "apply ohm's law for 12 V and 4 ohm",
    "how many molecules are in one mole of CO2",
])
def test_classify_science_intent(text):
    assert _intent.classify_intent(text).intent == "science"


@pytest.mark.parametrize("text,intent", [
    # a bare sum with no science term stays maths
    ("2+2", "math"),
    ("integrate sin(x)", "math"),
    # prose science still routes up to research/task by priority
    ("latest physics news", "research"),
    ("write an essay about chemistry", "task"),
])
def test_science_intent_does_not_hijack(text, intent):
    assert _intent.classify_intent(text).intent == intent


def test_science_routing_binds_compute_and_plot_tools():
    tools = _routing.tool_names_for_intent(
        "Calculate and verify the math result, then plot the function and data."
    )
    assert tools is not None
    assert {"calculate", "verify_math", "plot_function", "plot_data"} <= set(tools)


def test_science_brief_is_compute_first():
    brief = _prompts.SCIENCE_BRIEF
    assert "calculate" in brief
    assert "never" in brief.lower()
    assert "**Answer:**" in brief
    # plot tools are bound for science too
    chat_tools = _routing.tool_names_for_intent("Plot this function for me.")
    assert chat_tools is not None
    assert "plot_function" in chat_tools


def test_format_answer_is_answer_only():
    res = _solve("2+3")
    answer = me.format_answer(res)
    assert answer.startswith("> **Answer:**")
    assert "5" in answer
    assert "$$" not in answer          # no LaTeX derivation
    assert "\n1." not in answer        # no numbered steps


def test_format_answer_error_falls_back_to_full_reply():
    bad = me.MathResult(ok=False, error="No expression provided.")
    assert me.format_answer(bad) == me.format_reply(bad)


def test_pedagogy_render_show_working_false_is_answer_only():
    res = _solve("2+3")
    out = pedagogy.render(res, "primary", show_working=False)
    assert out.startswith("> **Answer:**")
    assert not out.startswith(pedagogy.LEVELS["primary"].header)
    assert out == me.format_answer(res)


def test_explain_math_show_working_false_skips_model(monkeypatch):
    """show_working=False returns answer-only even with explanation enabled: the
    model is never consulted when the user only wants the result."""
    import asyncio
    from typing import Any

    from app.services import science_explain

    class _BoomRouter:
        def __getattr__(self, _name):  # pragma: no cover - must not be called
            raise AssertionError("router must not be used when show_working=False")

    boom: Any = _BoomRouter()
    out = asyncio.run(science_explain.explain_math(
        router=boom, question="2+3", result=_solve("2+3"),
        show_working=False))
    assert out.startswith("> **Answer:**")
    assert "$$" not in out and "5" in out


def test_fast_path_per_request_level_override(client, monkeypatch):
    """The request body's `math_level` overrides the configured default."""
    from app.config import get_settings

    monkeypatch.setenv("JALORA_MATH_EXPLAIN", "false")
    monkeypatch.setenv("JALORA_MATH_LEVEL", "auto")
    get_settings.cache_clear()
    try:
        headers = _auth_headers(client, "math-level-override@test.com")
        resp = client.post(
            "/api/chat",
            json={"message": "2+2*5", "math_level": "university"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        reply = resp.json()["reply"]
        assert reply.startswith(pedagogy.LEVELS["university"].header)
        assert "12" in reply
    finally:
        get_settings.cache_clear()


def test_fast_path_per_request_show_working_false(client, monkeypatch):
    """The request body's `show_working=false` returns only the final answer."""
    from app.config import get_settings

    monkeypatch.setenv("JALORA_MATH_EXPLAIN", "true")
    monkeypatch.setenv("JALORA_MATH_LEVEL", "highschool")
    get_settings.cache_clear()
    try:
        headers = _auth_headers(client, "math-noworking@test.com")
        resp = client.post(
            "/api/chat",
            json={"message": "2+2*5", "show_working": False},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        reply = resp.json()["reply"]
        assert reply.startswith("> **Answer:**")
        assert "12" in reply
        assert "$$" not in reply
        assert not reply.startswith(pedagogy.LEVELS["highschool"].header)
    finally:
        get_settings.cache_clear()


