"""Deterministic finance calculations used by chat fast paths."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext


@dataclass(frozen=True)
class LoanResult:
    principal: float
    annual_rate: float
    term_months: int
    monthly_payment: float
    total_interest: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "principal": self.principal,
            "annual_rate": self.annual_rate,
            "term_months": self.term_months,
            "monthly_payment": round(self.monthly_payment, 2),
            "total_interest": round(self.total_interest, 2),
        }


_CENT = Decimal("0.01")


_PRINCIPAL_PATTERNS = (
    re.compile(r"\b(?:KES|KSh|Ksh)\s*([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\b(?:loan|principal|borrow(?:ed|ing)?)\s*(?:of|amount(?:ing)?\s+to)?\s*([\d,]+(?:\.\d+)?)", re.I),
)
_RATE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:annual|per\s+year|p\.a\.)?", re.I)
_YEARS = re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?\s*years?", re.I)
_MONTHS = re.compile(r"(\d+)\s*months?", re.I)


def parse_loan_request(text: str) -> tuple[float, float, int] | None:
    """Extract a fully specified fixed-rate monthly loan request."""
    raw = text or ""
    principal_match = next((pattern.search(raw) for pattern in _PRINCIPAL_PATTERNS if pattern.search(raw)), None)
    rate_match = _RATE.search(raw)
    if not principal_match or not rate_match:
        return None
    principal = float(principal_match.group(1).replace(",", ""))
    annual_rate = float(rate_match.group(1))
    month_match = _MONTHS.search(raw)
    year_match = _YEARS.search(raw)
    if month_match:
        term_months = int(month_match.group(1))
    elif year_match:
        term_months = round(float(year_match.group(1)) * 12)
    else:
        return None
    if principal <= 0 or annual_rate < 0 or term_months <= 0:
        return None
    return principal, annual_rate, term_months


def calculate_loan(principal: float, annual_rate: float, term_months: int) -> LoanResult:
    monthly_rate = annual_rate / 100 / 12
    if monthly_rate == 0:
        payment = principal / term_months
    else:
        payment = principal * monthly_rate / (1 - (1 + monthly_rate) ** (-term_months))
    return LoanResult(principal, annual_rate, term_months, payment,
                      payment * term_months - principal)


def build_amortization_schedule(
    principal: float, annual_rate: float, term_months: int
) -> list[dict[str, Decimal | int]]:
    """Build a cent-rounded monthly schedule with the final payment clearing the balance."""
    with localcontext() as context:
        context.prec = 28
        balance = Decimal(str(principal)).quantize(_CENT)
        monthly_rate = Decimal(str(annual_rate)) / Decimal(1200)
        if monthly_rate == 0:
            regular_payment = (balance / term_months).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
        else:
            exact_payment = (
                balance * monthly_rate
                / (1 - (1 + monthly_rate) ** -term_months)
            )
            regular_payment = exact_payment.quantize(
                _CENT, rounding=ROUND_HALF_UP
            )

        rows: list[dict[str, Decimal | int]] = []
        for month in range(1, term_months + 1):
            beginning_balance = balance
            interest = (balance * monthly_rate).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
            payment = (
                beginning_balance + interest
                if month == term_months
                else regular_payment
            )
            principal_paid = payment - interest
            balance = beginning_balance - principal_paid
            if month == term_months:
                balance = Decimal("0.00")
            rows.append({
                "month": month,
                "beginning_balance": beginning_balance,
                "payment": payment,
                "principal": principal_paid,
                "interest": interest,
                "ending_balance": balance,
            })
        return rows


def is_json_only_request(text: str) -> bool:
    return bool(re.search(r"\b(?:json only|return json|valid json)\b", text or "", re.I))


def format_loan_reply(result: LoanResult, *, json_only: bool) -> str:
    payload = result.as_dict()
    if json_only:
        return json.dumps(payload, separators=(",", ":"))
    return (
        "Using monthly compounding and no extra fees or margin:\n\n"
        f"- Principal: {payload['principal']:,.2f}\n"
        f"- Annual rate: {payload['annual_rate']:.4g}%\n"
        f"- Term: {payload['term_months']} months\n"
        f"- Monthly payment: {payload['monthly_payment']:,.2f}\n"
        f"- Total interest: {payload['total_interest']:,.2f}\n\n"
        "Formula: payment = P·r / (1 − (1 + r)^−n), where r is the monthly rate and n is the number of payments."
    )
