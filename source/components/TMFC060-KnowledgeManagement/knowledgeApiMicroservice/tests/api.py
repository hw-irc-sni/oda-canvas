"""An API under test: real Postgres (migrated), a temporary store, signed tokens."""
from contextlib import asynccontextmanager

import httpx
from sqlalchemy import text

from app.auth import TokenValidator
from app.config import Settings
from app.main import create_app

from .tokens import CLIENT, ISSUER, KeyPair, StaticJWKClient

KEY = KeyPair("api-key")


def token(*roles, **kw) -> str:
    return KEY.token(roles or ("knowledge-viewer",), **kw)


@asynccontextmanager
async def running_api(postgres_url: str, store_path: str, **overrides):
    from app.db.session import make_engine
    engine = make_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    await engine.dispose()
    overrides.setdefault("federation_relay_port", 0)  # a free loopback port per test app
    overrides.setdefault("internal_port", 0)
    overrides.setdefault("internal_host", "127.0.0.1")
    settings = Settings(component_name=CLIENT, store_path=store_path, database_url=postgres_url,
                        keycloak_jwks_url="http://jwks.invalid/certs", keycloak_issuers=[ISSUER], **overrides)
    app = create_app(settings, TokenValidator(settings, StaticJWKClient(KEY)))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://km.example.org") as client:
            client.app = app
            client.base = settings.base_path
            yield client


def auth(*roles, **kw) -> dict:
    return {"Authorization": f"Bearer {token(*roles, **kw)}"}
