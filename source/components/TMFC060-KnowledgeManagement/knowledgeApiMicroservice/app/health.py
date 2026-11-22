"""Dependency checks for /health (liveness, always 200) and /ready (readiness, 503 unless the required checks pass)."""
import asyncio
from dataclasses import dataclass

import httpx
import psycopg

from .config import Settings
from .store import get_store

REQUIRED = ("store", "postgres", "qdrant")


@dataclass
class Check:
    status: str  # "up", "down" or "not-configured"
    detail: str = ""

    def as_dict(self) -> dict:
        return {"status": self.status, **({"detail": self.detail} if self.detail else {})}


async def check_store(settings: Settings) -> Check:
    try:
        await asyncio.to_thread(lambda: get_store(settings.store_path).query("ASK {}"))
        return Check("up")
    except Exception as exc:  # noqa: BLE001 - any failure means the store is down
        return Check("down", f"{type(exc).__name__}: {exc}")


async def check_postgres(settings: Settings) -> Check:
    try:
        async with await psycopg.AsyncConnection.connect(
            settings.database_url, connect_timeout=max(1, int(settings.health_check_timeout))
        ) as conn:
            await conn.execute("SELECT 1")
        return Check("up")
    except Exception as exc:  # noqa: BLE001
        return Check("down", f"{type(exc).__name__}: {exc}")


async def check_qdrant(settings: Settings) -> Check:
    try:
        async with httpx.AsyncClient(timeout=settings.health_check_timeout) as client:
            response = await client.get(f"{settings.qdrant_url.rstrip('/')}/readyz")
        return Check("up") if response.status_code == 200 else Check("down", f"HTTP {response.status_code}")
    except Exception as exc:  # noqa: BLE001
        return Check("down", f"{type(exc).__name__}: {exc}")


async def check_model_route(settings: Settings) -> Check:
    # The model resolver chain arrives with #34; until then there is no route to report.
    return Check("not-configured")


async def run_checks(settings: Settings) -> dict[str, Check]:
    names = ("store", "postgres", "qdrant", "modelRoute")
    results = await asyncio.gather(
        check_store(settings), check_postgres(settings), check_qdrant(settings), check_model_route(settings)
    )
    return dict(zip(names, results))


def is_ready(checks: dict[str, Check]) -> bool:
    return all(checks[name].status == "up" for name in REQUIRED)
