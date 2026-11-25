"""The async engine and sessions."""
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def async_url(database_url: str) -> str:
    """postgresql://... -> postgresql+psycopg://... (psycopg 3, async)."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix):]
    raise ValueError("DATABASE_URL must be a postgresql:// URL")


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(async_url(database_url), pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
