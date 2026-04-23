# ADR 0001 — Database Client: asyncpg (direct)

**Status:** Accepted  
**Date:** 2026-04-22

## Context

The stack uses Supabase (Postgres) as its database. Three options were evaluated for the async Python DB client: `supabase-py`, `SQLAlchemy + asyncpg`, and `asyncpg` directly.

## Decision

Use **asyncpg** directly, without an ORM.

## Reasoning

**Against supabase-py as primary DB client:** supabase-py is a REST/PostgREST wrapper — its query API is weaker than raw SQL for complex filters, window functions, and Postgres-specific types (JSONB, custom enums, `text[]`, pgvector). It also adds a network hop via PostgREST. It remains useful for Storage and Realtime, but not for the hot path.

**Against SQLAlchemy + asyncpg:** SQLAlchemy ORM would add significant complexity and mapping boilerplate for a schema that uses non-standard Postgres types (pgvector, custom enums, JSONB). The benefit — generating SQL — is low when queries are already simple or involve features ORM handles poorly. Core mode (without ORM) would work but still adds a layer with no payoff.

**For asyncpg directly:** Raw SQL with asyncpg gives direct access to Postgres type codecs (JSONB, UUID, enums, arrays), the fastest connection pooling in the Python ecosystem, and zero impedance mismatch for this schema. supabase-py can still be added later for Storage or Realtime without touching the DB layer.

## Trade-offs

Downside: more verbose query code; no migration tooling (Supabase CLI handles that). Acceptable for this phase.
