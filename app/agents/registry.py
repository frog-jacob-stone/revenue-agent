from typing import Any

# Keyed by slug. These are the code-owned fields — system_prompt and is_active
# are operator-owned and will never be overwritten by the seeder.
AGENT_REGISTRY: dict[str, dict[str, Any]] = {
    "sdr-researcher": {
        "name": "SDR Researcher",
        "description": (
            "Researches new accounts from HubSpot or manual triggers. "
            "Enriches contact and company data via Apollo."
        ),
        "requires_approval": True,
        "allowed_tools": ["apollo_search", "hubspot_read"],
        "config": {},
    },
    "outreach-agent": {
        "name": "Outreach Agent",
        "description": (
            "Drafts and queues personalised outreach emails after SDR Researcher completes."
        ),
        "requires_approval": True,
        "allowed_tools": ["gmail_draft", "hubspot_read", "hubspot_update"],
        "config": {},
    },
    "content-writer": {
        "name": "Content Writer",
        "description": "Produces marketing and thought-leadership content on a manual or scheduled trigger.",
        "requires_approval": True,
        "allowed_tools": ["hubspot_read"],
        "config": {},
    },
    "proposal-generator": {
        "name": "Proposal Generator",
        "description": "Generates client proposals when a HubSpot deal advances to the proposal stage.",
        "requires_approval": True,
        "allowed_tools": ["hubspot_read", "hubspot_update"],
        "config": {},
    },
    "slide-deck-agent": {
        "name": "Slide Deck Agent",
        "description": "Converts a completed proposal into a presentation deck.",
        "requires_approval": True,
        "allowed_tools": ["hubspot_read"],
        "config": {},
    },
    "revenue-recognition": {
        "name": "Revenue Recognition",
        "description": "Runs monthly revenue recognition calculations and writes journal entries.",
        "requires_approval": True,
        "allowed_tools": ["hubspot_read"],
        "config": {},
    },
}
