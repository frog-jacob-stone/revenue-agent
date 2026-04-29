from typing import Any

from app.services import clients as clients_service
from app.services import invoices as invoices_service
from app.services import projects as projects_service
from app.tools.base import ToolContext, ToolDefinition


async def _list_harvest_clients(ctx: ToolContext, **_: Any) -> list[dict[str, Any]]:
    return await clients_service.list_active_clients()


LIST_HARVEST_CLIENTS = ToolDefinition(
    name="list_harvest_clients",
    description="List all active Harvest clients. Use this to look up a client ID by name.",
    input_schema={"type": "object", "properties": {}},
    execute=_list_harvest_clients,
)


async def _list_client_projects(ctx: ToolContext, *, client_id: int, **_: Any) -> list[dict[str, Any]]:
    return await projects_service.list_active_projects_for_client(int(client_id))


LIST_CLIENT_PROJECTS = ToolDefinition(
    name="list_client_projects",
    description="List active Harvest projects for a specific client.",
    input_schema={
        "type": "object",
        "properties": {
            "client_id": {"type": "integer", "description": "Harvest client ID."},
        },
        "required": ["client_id"],
    },
    execute=_list_client_projects,
)


async def _list_invoices(
    ctx: ToolContext,
    *,
    client_id: int | None = None,
    status: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    return await invoices_service.list_invoices(
        client_id=client_id, status=status, from_date=from_date, to_date=to_date
    )


LIST_INVOICES = ToolDefinition(
    name="list_invoices",
    description=(
        "List Harvest invoices, optionally filtered by client, status, or date range. "
        "Returns summary fields including client, amount, status, issue date, due date, paid date."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "client_id": {"type": "integer", "description": "Filter by Harvest client ID."},
            "status": {
                "type": "string",
                "enum": ["draft", "open", "paid", "closed"],
                "description": "Filter by invoice status.",
            },
            "from_date": {
                "type": "string",
                "description": "Filter invoices issued on or after this date, ISO YYYY-MM-DD.",
            },
            "to_date": {
                "type": "string",
                "description": "Filter invoices issued on or before this date, ISO YYYY-MM-DD.",
            },
        },
    },
    execute=_list_invoices,
)


async def _get_invoice_details(ctx: ToolContext, *, invoice_id: int, **_: Any) -> dict[str, Any]:
    return await invoices_service.get_invoice_details(int(invoice_id))


GET_INVOICE_DETAILS = ToolDefinition(
    name="get_invoice_details",
    description="Get full details of a specific invoice including all line items.",
    input_schema={
        "type": "object",
        "properties": {
            "invoice_id": {"type": "integer", "description": "Harvest invoice ID."},
        },
        "required": ["invoice_id"],
    },
    execute=_get_invoice_details,
)


async def _get_unbilled_time_entries(
    ctx: ToolContext,
    *,
    client_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    return await invoices_service.get_unbilled_time_entries(
        client_id=client_id, from_date=from_date, to_date=to_date
    )


GET_UNBILLED_TIME_ENTRIES = ToolDefinition(
    name="get_unbilled_time_entries",
    description=(
        "Find billable time entries that have not yet been invoiced. "
        "Useful for gap analysis: 'what time is unbilled?' or 'are there entries older than 30 days that haven't been invoiced?'"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "client_id": {"type": "integer", "description": "Filter by Harvest client ID."},
            "from_date": {"type": "string", "description": "Start date filter, ISO YYYY-MM-DD."},
            "to_date": {"type": "string", "description": "End date filter, ISO YYYY-MM-DD."},
        },
    },
    execute=_get_unbilled_time_entries,
)
