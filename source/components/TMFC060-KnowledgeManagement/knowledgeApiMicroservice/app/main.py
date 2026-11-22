"""FastAPI application of the TMFC060 knowledge API. Run with: uvicorn --factory app.main:create_app"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import TokenValidator, check_auth_settings, load_operations
from .config import Settings, get_settings
from .db.migrate import upgrade, wait_for_database
from .db.session import make_engine, make_sessionmaker
from .events import Dispatcher, HubEvents
from .graphstore import GraphStore
from .ontology import sync
from .ontology.governance import load_lifecycles
from .ontology.registry import Registry
from qdrant_client import AsyncQdrantClient

from .kb import router as kb_router
from .internal import InternalServer
from .models import ModelResolver
from .ontology.federation import Relay
from .routers import hub, processes, specifications, terms
from .store import get_store
from .errors import ApiError
from .health import is_ready, run_checks


def create_app(settings: Settings | None = None, token_validator: TokenValidator | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(format="%(levelname)s:     %(name)s: %(message)s")
    logging.getLogger("app").setLevel(settings.log_level.upper())
    check_auth_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        await wait_for_database(engine, settings.db_startup_timeout)
        await upgrade(engine)
        app.state.engine = engine
        app.state.sessions = make_sessionmaker(engine)
        app.state.graphs = GraphStore(get_store(settings.store_path))
        app.state.qdrant = AsyncQdrantClient(url=settings.qdrant_url, timeout=10)
        app.state.events.sessions = app.state.sessions
        app.state.dispatcher = Dispatcher(settings, app.state.sessions)
        dispatch_task = asyncio.create_task(app.state.dispatcher.run_forever())
        app.state.internal = InternalServer(app)
        await app.state.internal.start()
        app.state.relay = Relay(settings, app.state.sessions)
        await app.state.relay.start()
        app.state.models = ModelResolver(settings)
        models_task = asyncio.create_task(app.state.models.run_periodically())
        sources = sync.parse_sources(settings.ontology_sources)
        sync_task = None
        if sources:
            sync_task = asyncio.create_task(sync.run_periodically(
                lambda: _registry_context(app), sources, settings.ontology_sync_interval))
        try:
            yield
        finally:
            if sync_task:
                sync_task.cancel()
            models_task.cancel()
            dispatch_task.cancel()
            await app.state.dispatcher.aclose()
            await app.state.models.aclose()
            await app.state.relay.stop()
            await app.state.internal.stop()
            await app.state.qdrant.close()
            await engine.dispose()

    app = FastAPI(title="Knowledge Management", version="5.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.operations = {**load_operations(settings.oas_path, settings.base_path),
                            **load_operations(settings.process_oas_path, settings.process_base_path)}
    app.state.token_validator = token_validator or (TokenValidator(settings) if settings.auth_enabled else None)
    app.state.events = HubEvents(settings)
    app.state.lifecycles = load_lifecycles(settings.oas_path)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        media_type = "application/problem+json" if "application/problem+json" in request.headers.get("accept", "") \
            else "application/json"
        return JSONResponse(exc.body(request.url.path), status_code=exc.status_code, headers=exc.headers,
                            media_type=media_type)

    @app.get("/health", include_in_schema=False)
    async def health() -> JSONResponse:
        checks = await run_checks(settings, getattr(app.state, "models", None))
        body = {"status": "ready" if is_ready(checks) else "degraded",
                "checks": {name: check.as_dict() for name, check in checks.items()}}
        return JSONResponse(body, status_code=200)

    @app.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        checks = await run_checks(settings, getattr(app.state, "models", None))
        ok = is_ready(checks)
        body = {"status": "ready" if ok else "not-ready",
                "checks": {name: check.as_dict() for name, check in checks.items()}}
        return JSONResponse(body, status_code=200 if ok else 503)

    # The TMFxxx resources are added to this router by the following issues.
    api = APIRouter(prefix=settings.base_path)
    api.include_router(specifications.router)
    api.include_router(terms.router)
    api.include_router(hub.router)
    api.include_router(kb_router.router)
    app.include_router(api)
    app.include_router(processes.router, prefix=settings.process_base_path)
    app.state.api_router = api
    return app



@asynccontextmanager
async def _registry_context(app: FastAPI):
    """A Registry on its own session, for background work such as the ontology source sync."""
    async with app.state.sessions() as session:
        yield Registry(session, app.state.graphs, app.state.settings, app.state.events)
