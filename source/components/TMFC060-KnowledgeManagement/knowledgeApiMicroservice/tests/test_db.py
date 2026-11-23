"""Schema migrations (Alembic, advisory lock) and the chunked document blobs."""
import asyncio
import hashlib

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select, text

from app.db.migrate import upgrade, downgrade, wait_for_database
from app.db.models import BLOB_CHUNK_SIZE, Base, Document, DocumentBlob, Knowledge
from app.db.session import async_url, make_engine, make_sessionmaker
from app.main import create_app

pytestmark = pytest.mark.integration
TABLES = {"knowledge", "knowledge_specification", "mapping", "document", "document_blob", "process", "task",
          "review_item", "state_transition", "hub_subscription", "recommend_feedback", "validation_report"}
HEAD = "0007_knowledge_proposal"


@pytest.fixture
async def engine(postgres_url):
    engine = make_engine(postgres_url)
    async with engine.begin() as conn:  # start every test from an empty database
        await conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    yield engine
    await engine.dispose()


async def tables(engine):
    async with engine.connect() as conn:
        return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))


async def revision(engine):
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()


async def test_upgrade_on_an_empty_database_creates_the_schema(engine):
    await upgrade(engine)
    assert TABLES <= await tables(engine)
    assert await revision(engine) == HEAD


async def test_upgrade_is_idempotent(engine):
    await upgrade(engine)
    await upgrade(engine)
    assert await revision(engine) == HEAD


async def test_migrated_schema_matches_the_models(engine):
    await upgrade(engine)
    async with engine.connect() as conn:
        diff = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata))
    assert diff == []


async def test_downgrade_of_the_last_revision_and_upgrade_again(engine):
    await upgrade(engine)
    await downgrade(engine, "-1")
    assert await revision(engine) == "0006_event_delivery"
    await upgrade(engine)
    assert TABLES <= await tables(engine)


async def test_concurrent_upgrades_serialise_on_the_advisory_lock(engine, postgres_url):
    other = make_engine(postgres_url)
    try:
        await asyncio.gather(upgrade(engine), upgrade(other))
    finally:
        await other.dispose()
    assert await revision(engine) == HEAD


async def test_document_blob_chunks_round_trip_and_cascade(engine):
    await upgrade(engine)
    content = bytes(range(256)) * (BLOB_CHUNK_SIZE // 256 * 2 + 10)  # a bit more than 2 chunks
    sessions = make_sessionmaker(engine)
    async with sessions() as session:
        session.add(Knowledge(id="kb1", name="kb", lifecycle_status="draft", store_kind="hybrid", body={}))
        await session.flush()  # no ORM relationships: insert the parent before the child
        session.add(Document(id="d1", knowledge_id="kb1", name="f.pdf", media_type="application/pdf",
                             size=len(content), sha256=hashlib.sha256(content).hexdigest()))
        await session.flush()
        for seq, start in enumerate(range(0, len(content), BLOB_CHUNK_SIZE)):
            session.add(DocumentBlob(document_id="d1", seq=seq, data=content[start:start + BLOB_CHUNK_SIZE]))
        await session.commit()
    async with sessions() as session:
        chunks = (await session.scalars(select(DocumentBlob.data).where(DocumentBlob.document_id == "d1")
                                        .order_by(DocumentBlob.seq))).all()
        assert len(chunks) == 3 and b"".join(chunks) == content
        await session.delete(await session.get(Knowledge, "kb1"))
        await session.commit()
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DocumentBlob)) == 0


async def test_app_startup_runs_the_migrations(settings, engine):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert app.state.sessions is not None
    assert await revision(engine) == HEAD


async def test_startup_gives_up_after_the_timeout():
    unreachable = make_engine("postgresql://x:y@127.0.0.1:9/none")
    with pytest.raises(RuntimeError, match="not reachable after"):
        await wait_for_database(unreachable, timeout=1.5)
    await unreachable.dispose()


def test_async_url():
    assert async_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    with pytest.raises(ValueError):
        async_url("mysql://h/db")
