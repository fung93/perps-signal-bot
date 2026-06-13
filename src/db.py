"""Neon (serverless Postgres) access for the Perps Signal Bot.

All database access goes through this module (see CLAUDE.md). Uses psycopg v3 with
Neon's **pooled** connection string. Neon scales to zero when idle, so the first connect
after an idle period can take ~1s or fail once — connecting is wrapped in a short retry.

Conventions:
  * Use the pooled URL (host contains ``-pooler``).
  * Batch each run's DB work into a single connection (conserves Neon compute-hours)::

        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(...)   # all the run's queries here

  * Parameterised queries only — never build SQL with f-strings.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from . import config

logger = logging.getLogger(__name__)

# Cold-start retry: a Neon scale-to-zero wake can make the first connect slow or fail once.
_CONNECT_RETRIES = 3
_CONNECT_BACKOFF_SECONDS = 1.5
_CONNECT_TIMEOUT_SECONDS = 10


def _database_url() -> str:
    """Return the Neon connection URL, warning if it is not the pooled endpoint."""
    url = config.require_env("NEON_DATABASE_URL")
    if "-pooler" not in url:
        logger.warning(
            "NEON_DATABASE_URL host has no '-pooler' — use the Neon POOLED "
            "connection string for serverless / Actions use."
        )
    return url


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Yield one Neon connection, retrying the cold-start connect.

    Commits on clean exit, rolls back on exception, and always closes. Do all of a
    run's DB work inside a single ``with db.connect()`` block.
    """
    url = _database_url()
    conn: psycopg.Connection | None = None
    last_error: Exception | None = None
    for attempt in range(1, _CONNECT_RETRIES + 1):
        try:
            conn = psycopg.connect(url, connect_timeout=_CONNECT_TIMEOUT_SECONDS)
            break
        except psycopg.OperationalError as exc:
            last_error = exc
            wait = _CONNECT_BACKOFF_SECONDS * attempt
            logger.warning(
                "Neon connect attempt %d/%d failed (%s); retrying in %.1fs "
                "(scale-to-zero cold start?)",
                attempt,
                _CONNECT_RETRIES,
                exc,
                wait,
            )
            time.sleep(wait)
    if conn is None:
        raise ConnectionError(
            f"Could not connect to Neon after {_CONNECT_RETRIES} attempts."
        ) from last_error

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping() -> bool:
    """Round-trip ``SELECT 1`` to confirm the DB is reachable. Returns True on success."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            row = cur.fetchone()
    return row is not None and row[0] == 1


def fetch_all(sql: str, params: tuple | None = None) -> list[tuple]:
    """Run a parameterised read query in its own connection and return all rows.

    Convenience for one-off reads. For multi-query runs, open a single
    ``with db.connect()`` block instead to conserve Neon compute-hours.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
