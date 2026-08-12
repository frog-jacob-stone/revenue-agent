import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.services import audit

logger = logging.getLogger(__name__)


async def save_post(
    pool: asyncpg.Pool,
    *,
    topic: str,
    idea_title: str | None = None,
    core_angle: str | None = None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO social_posts (topic, idea_title, core_angle)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            topic,
            idea_title,
            core_angle,
        )
        result = dict(row)
        await audit.write_audit_event(
            conn,
            "content.post_created",
            payload={"post_id": str(result["id"]), "topic": topic},
        )
        return result


async def get_post(pool: asyncpg.Pool, post_id: UUID) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM social_posts WHERE id = $1", post_id)
        return dict(row) if row else None


async def get_posts_by_status(
    pool: asyncpg.Pool, status: str
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM social_posts WHERE status = $1 ORDER BY created_at ASC",
            status,
        )
        return [dict(r) for r in rows]


async def update_post(
    pool: asyncpg.Pool,
    post_id: UUID,
    **fields: Any,
) -> dict[str, Any]:
    if not fields:
        raise ValueError("No fields provided to update_post")

    set_clauses = ", ".join(
        f"{col} = ${i + 2}" for i, col in enumerate(fields)
    )
    values = list(fields.values())

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE social_posts SET {set_clauses} WHERE id = $1 RETURNING *",
            post_id,
            *values,
        )
        if row is None:
            raise ValueError(f"Post {post_id} not found")
        result = dict(row)
        await audit.write_audit_event(
            conn,
            "content.post_updated",
            payload={"post_id": str(post_id), "fields": list(fields.keys())},
        )
        return result


async def update_post_status(
    pool: asyncpg.Pool,
    post_id: UUID,
    new_status: str,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE social_posts SET status = $1 WHERE id = $2 RETURNING *",
            new_status,
            post_id,
        )
        if row is None:
            raise ValueError(f"Post {post_id} not found")
        result = dict(row)

        event = _status_audit_event(new_status)
        await audit.write_audit_event(
            conn,
            event,
            payload={"post_id": str(post_id), "status": new_status},
        )
        return result


async def update_posts_status(
    pool: asyncpg.Pool,
    post_ids: list[UUID],
    new_status: str,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "UPDATE social_posts SET status = $1 WHERE id = ANY($2) RETURNING *",
            new_status,
            post_ids,
        )
        results = [dict(r) for r in rows]
        event = _status_audit_event(new_status)
        await audit.write_audit_event(
            conn,
            event,
            payload={"post_ids": [str(pid) for pid in post_ids], "status": new_status},
        )
        return results


async def create_post_conn(
    conn: asyncpg.Connection,
    *,
    topic: str,
    idea_title: str | None = None,
    core_angle: str | None = None,
    post_text: str | None = None,
    status: str = "draft",
) -> UUID:
    """Insert a new social_posts row using an existing connection. Returns the new id."""
    row = await conn.fetchrow(
        """
        INSERT INTO social_posts (topic, idea_title, core_angle, post_text, status)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        topic,
        idea_title,
        core_angle,
        post_text,
        status,
    )
    post_id: UUID = row["id"]
    await audit.write_audit_event(
        conn,
        "content.post_created",
        payload={"post_id": str(post_id), "topic": topic},
    )
    return post_id


async def get_post_conn(conn: asyncpg.Connection, post_id: UUID) -> dict[str, Any] | None:
    row = await conn.fetchrow("SELECT * FROM social_posts WHERE id = $1", post_id)
    return dict(row) if row else None


async def update_post_conn(conn: asyncpg.Connection, post_id: UUID, **fields: Any) -> None:
    if not fields:
        return
    set_clauses = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(fields))
    await conn.execute(
        f"UPDATE social_posts SET {set_clauses} WHERE id = $1",
        post_id,
        *fields.values(),
    )
    await audit.write_audit_event(
        conn,
        "content.post_updated",
        payload={"post_id": str(post_id), "fields": list(fields.keys())},
    )


def _status_audit_event(status: str) -> str:
    return {
        "draft": "content.post_drafted",
        "approved": "content.post_approved",
        "rejected": "content.post_rejected",
    }.get(status, "content.post_updated")
