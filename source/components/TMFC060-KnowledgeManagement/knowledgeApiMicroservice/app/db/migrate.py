"""Alembic migrations, run at API startup under a Postgres advisory lock (one migrator at a time)."""
import asyncio
import logging
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

# An arbitrary 64-bit key that identifies "TMFC060 schema migration" among advisory locks.
MIGRATION_LOCK_KEY = 0x54_4D_46_43_30_36_30  # "TMFC060"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(connection=None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _upgrade(sync_connection, revision: str) -> None:
    command.upgrade(alembic_config(sync_connection), revision)


def _downgrade(sync_connection, revision: str) -> None:
    command.downgrade(alembic_config(sync_connection), revision)


async def _locked(conn: AsyncConnection, func, revision: str) -> None:
    await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
    try:
        await conn.run_sync(func, revision)
        await conn.commit()
    finally:
        await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
        await conn.commit()


async def upgrade(engine: AsyncEngine, revision: str = "head") -> None:
    async with engine.connect() as conn:
        await _locked(conn, _upgrade, revision)


async def downgrade(engine: AsyncEngine, revision: str) -> None:
    async with engine.connect() as conn:
        await _locked(conn, _downgrade, revision)


log = logging.getLogger(__name__)


async def wait_for_database(engine: AsyncEngine, timeout: float) -> None:
    """Retry connecting with exponential backoff (1 s up to 10 s) until `timeout` seconds have passed."""
    deadline = time.monotonic() + timeout
    delay = 1.0
    attempt = 0
    while True:
        attempt += 1
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - any connection failure is retried
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Postgres not reachable after {timeout:.0f} s: {exc}") from exc
            log.warning("Postgres not reachable (attempt %d): %s; retrying in %.0f s", attempt, exc, delay)
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 2, 10.0)
