from typing import Any

from app.services import invoices as invoices_service
from app.tools.base import ToolContext, ToolDefinition


async def _get_pending_invoice_approvals(ctx: ToolContext, **_: Any) -> list[dict[str, Any]]:
    return await invoices_service.get_pending_invoice_approvals()


GET_PENDING_INVOICE_APPROVALS = ToolDefinition(
    name="get_pending_invoice_approvals",
    description=(
        "Return invoice actions currently sitting in the Approval Inbox "
        "(status=proposed). Answers questions like 'what invoices are awaiting approval?'"
    ),
    input_schema={"type": "object", "properties": {}},
    execute=_get_pending_invoice_approvals,
)
