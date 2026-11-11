"""Payment tools (A§ payments & travel).

`send_payment` is a STRICT action — it moves real money and requires explicit
user approval + extra scrutiny (see ``app/tools/base.py``).  Mock-first: without
a configured Stripe account it returns an immediate deterministic receipt.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services import payments
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


async def send_payment(recipient: str, amount: float, currency: str = "usd",
                       description: str = "") -> ToolResult:
    """Send money to a recipient (phone, email, contact name, or account id).
    Always pauses for your explicit confirmation before any funds are moved.
    """
    if not recipient or not str(recipient).strip():
        return ToolResult.fail(
            "INVALID_RECIPIENT", "A recipient is required.",
            message="Provide a recipient (phone, email, name, or account id).",
        )
    try:
        receipt = await payments.send_payment(
            user_id=_user_id(),
            recipient=recipient,
            amount=amount,
            currency=currency,
            description=description,
        )
    except payments.PaymentError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"Payment of {receipt.amount} {receipt.currency} queued to {receipt.to} "
        f"via {receipt.provider} ({receipt.receipt_id}).",
        receipt.as_dict(),
    )


registry.register(send_payment, permission=PermissionLevel.STRICT,
                  category="payments", requires_internet=True,
                  tags=["payments", "external"])
