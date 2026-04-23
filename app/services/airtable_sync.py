import logging

from app.config import Settings
from app.integrations import airtable, harvest

logger = logging.getLogger(__name__)


async def sync_clients(cfg: Settings) -> None:
    """Upsert all active Harvest clients into Airtable Clients table."""
    clients = await harvest.get_clients(cfg)
    records = [{"Harvest Id": c["id"], "Name": c["name"]} for c in clients]
    if not records:
        return
    await airtable.upsert_records(cfg, cfg.airtable_clients_table_id, records, ["Harvest Id"])
    logger.info("synced %d clients to Airtable", len(records))


async def sync_projects(cfg: Settings) -> None:
    """
    Upsert active billable Harvest projects (excluding Frogslayer internal) into
    Airtable Projects table. Only Harvest Id and Project Name are written — Billing
    Type, Contracted Fees, and Client Id are managed manually in Airtable.
    """
    projects = await harvest.get_active_projects(cfg)
    billable = [
        p for p in projects
        if p.get("is_billable")
        and (p.get("client") or {}).get("name") != "Frogslayer"
    ]
    records = [
        {"Harvest Id": p["id"], "Project Name": p["name"]}
        for p in billable
    ]
    if not records:
        return
    await airtable.upsert_records(cfg, cfg.airtable_projects_table_id, records, ["Harvest Id"])
    logger.info("synced %d projects to Airtable", len(records))


async def run_sync(cfg: Settings) -> None:
    """Sync clients then projects (clients first so relationships resolve correctly)."""
    await sync_clients(cfg)
    await sync_projects(cfg)
