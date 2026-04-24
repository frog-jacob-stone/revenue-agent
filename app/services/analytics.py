from __future__ import annotations

from datetime import date, timedelta

import asyncpg
from pydantic import BaseModel


class SummaryStats(BaseModel):
    accountsResearched: int
    outreachSent: int
    proposalsGenerated: int
    approvalRate: float
    avgTimeToApprove: str
    mostActiveAgent: str


class ApprovalRateRow(BaseModel):
    agent: str
    rate: float


class AnalyticsResponse(BaseModel):
    summaryStats: SummaryStats
    dailyRuns: list[dict]
    approvalRates: list[ApprovalRateRow]


_SUMMARY_SQL = """
WITH base AS (
    SELECT
        COUNT(*) FILTER (WHERE action_type = 'research'          AND status = 'completed') AS accounts_researched,
        COUNT(*) FILTER (WHERE action_type = 'send_email'        AND status = 'completed') AS outreach_sent,
        COUNT(*) FILTER (WHERE action_type = 'generate_document' AND status = 'completed') AS proposals_generated,
        COUNT(*) FILTER (WHERE status IN ('approved', 'completed'))                         AS approved_count,
        COUNT(*) FILTER (WHERE status IN ('approved', 'completed', 'rejected'))             AS decidable_count,
        AVG(EXTRACT(EPOCH FROM (approved_at - created_at)) / 60.0)
            FILTER (WHERE approved_at IS NOT NULL)                                          AS avg_mins
    FROM actions
)
SELECT
    accounts_researched,
    outreach_sent,
    proposals_generated,
    CASE WHEN decidable_count = 0 THEN 0.0
         ELSE ROUND((approved_count::numeric / decidable_count) * 100, 1)
    END AS approval_rate,
    avg_mins
FROM base
"""

_MOST_ACTIVE_SQL = """
SELECT ag.name
FROM actions a
JOIN agents ag ON ag.id = a.agent_id
WHERE a.status = 'completed'
GROUP BY ag.name
ORDER BY COUNT(*) DESC
LIMIT 1
"""

_DAILY_SQL = """
SELECT ag.name AS agent_name, a.created_at::date AS run_date, COUNT(*) AS cnt
FROM actions a
JOIN agents ag ON ag.id = a.agent_id
WHERE a.created_at >= (CURRENT_DATE - ($1 - 1) * INTERVAL '1 day')
GROUP BY ag.name, a.created_at::date
ORDER BY run_date, agent_name
"""

_APPROVAL_RATES_SQL = """
SELECT
    ag.name,
    COUNT(*) FILTER (WHERE a.status IN ('approved', 'completed'))            AS approved_count,
    COUNT(*) FILTER (WHERE a.status IN ('approved', 'completed', 'rejected')) AS decidable_count
FROM actions a
JOIN agents ag ON ag.id = a.agent_id
GROUP BY ag.name
ORDER BY ag.name
"""


def _format_avg_mins(avg_mins: float | None) -> str:
    if avg_mins is None:
        return "N/A"
    return f"{round(avg_mins)} min"


async def get_analytics(pool: asyncpg.Pool, days: int = 30) -> AnalyticsResponse:
    async with pool.acquire() as conn:
        summary_row = await conn.fetchrow(_SUMMARY_SQL)
        agent_row = await conn.fetchrow(_MOST_ACTIVE_SQL)
        daily_rows = await conn.fetch(_DAILY_SQL, days)
        rates_rows = await conn.fetch(_APPROVAL_RATES_SQL)

    # Build summary stats
    summary = SummaryStats(
        accountsResearched=summary_row["accounts_researched"] or 0,
        outreachSent=summary_row["outreach_sent"] or 0,
        proposalsGenerated=summary_row["proposals_generated"] or 0,
        approvalRate=float(summary_row["approval_rate"] or 0.0),
        avgTimeToApprove=_format_avg_mins(summary_row["avg_mins"]),
        mostActiveAgent=agent_row["name"] if agent_row else "N/A",
    )

    # Build daily runs — pivot rows into [{date, agentName: count, ...}]
    today = date.today()
    date_range = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]

    counts: dict[str, dict[str, int]] = {d: {} for d in date_range}
    for row in daily_rows:
        d = row["run_date"].isoformat()
        if d in counts:
            counts[d][row["agent_name"]] = row["cnt"]

    daily_runs: list[dict] = [{"date": d, **counts[d]} for d in date_range]

    # Build approval rates
    approval_rates = [
        ApprovalRateRow(
            agent=row["name"],
            rate=round(row["approved_count"] / row["decidable_count"] * 100, 1)
            if row["decidable_count"] > 0
            else 0.0,
        )
        for row in rates_rows
    ]

    return AnalyticsResponse(
        summaryStats=summary,
        dailyRuns=daily_runs,
        approvalRates=approval_rates,
    )
