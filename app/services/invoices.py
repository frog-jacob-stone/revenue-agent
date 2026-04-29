from typing import Any

from app.config import settings
from app.db import get_pool
from app.integrations.harvest import _get_all, _get_one


async def list_invoices(
    *,
    client_id: int | None = None,
    status: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """List Harvest invoices with optional client/status/date filters."""
    params: dict[str, Any] = {}
    if client_id is not None:
        params["client_id"] = client_id
    if status:
        params["status"] = status
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    invoices = await _get_all(settings, "/invoices", "invoices", params or None)
    return [
        {
            "id": inv["id"],
            "number": inv.get("number"),
            "client": (inv.get("client") or {}).get("name"),
            "status": inv.get("state"),
            "amount": inv.get("amount"),
            "due_amount": inv.get("due_amount"),
            "issue_date": inv.get("issue_date"),
            "due_date": inv.get("due_date"),
            "paid_date": inv.get("paid_at"),
            "payment_term": inv.get("payment_term"),
        }
        for inv in invoices
    ]


async def get_invoice_details(invoice_id: int) -> dict[str, Any]:
    inv = await _get_one(settings, f"/invoices/{invoice_id}")
    return {
        "id": inv["id"],
        "number": inv.get("number"),
        "client": (inv.get("client") or {}).get("name"),
        "status": inv.get("state"),
        "amount": inv.get("amount"),
        "due_amount": inv.get("due_amount"),
        "issue_date": inv.get("issue_date"),
        "due_date": inv.get("due_date"),
        "paid_date": inv.get("paid_at"),
        "payment_term": inv.get("payment_term"),
        "notes": inv.get("notes"),
        "line_items": [
            {
                "kind": li.get("kind"),
                "description": li.get("description"),
                "quantity": li.get("quantity"),
                "unit_price": li.get("unit_price"),
                "amount": li.get("amount"),
                "project": (li.get("project") or {}).get("name"),
            }
            for li in (inv.get("line_items") or [])
        ],
    }


async def get_unbilled_time_entries(
    *,
    client_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"is_billed": "false", "billable": "true"}
    if client_id is not None:
        params["client_id"] = client_id
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    entries = await _get_all(settings, "/time_entries", "time_entries", params)
    return [
        {
            "id": e["id"],
            "date": e.get("spent_date"),
            "client": (e.get("client") or {}).get("name"),
            "project": (e.get("project") or {}).get("name"),
            "task": (e.get("task") or {}).get("name"),
            "hours": e.get("hours"),
            "billable_rate": e.get("billable_rate"),
            "billable_amount": round(
                float(e.get("hours") or 0) * float(e.get("billable_rate") or 0), 2
            ),
            "notes": e.get("notes"),
        }
        for e in entries
    ]


async def get_pending_invoice_approvals() -> list[dict[str, Any]]:
    """Return invoice-related actions currently awaiting approval."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT a.id, a.summary, a.proposed_payload, a.created_at, a.risk_level
        FROM actions a
        WHERE a.action_type IN ('generate_invoice', 'send_invoice', 'delete_invoice')
          AND a.status = 'proposed'
        ORDER BY a.created_at DESC
        """
    )
    return [
        {
            "action_id": str(r["id"]),
            "summary": r["summary"],
            "risk_level": r["risk_level"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "client_name": (r["proposed_payload"] or {}).get("client_name"),
            "subtotal": (r["proposed_payload"] or {}).get("subtotal"),
        }
        for r in rows
    ]
