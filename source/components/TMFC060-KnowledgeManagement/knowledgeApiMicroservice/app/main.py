"""FastAPI application of the TMFC060 knowledge API. Run with: uvicorn --factory app.main:create_app"""
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import TokenValidator, check_auth_settings, load_operations
from .config import Settings, get_settings
from .db.migrate import upgrade, wait_for_database
from .db.session import make_engine, make_sessionmaker
from .errors import ApiError
from .health import is_ready, run_checks


def create_app(settings: Settings | None = None, token_validator: TokenValidator | None = None) -> FastAPI:
    settings = settings or get_settings()
    check_auth_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        await wait_for_database(engine, settings.db_startup_timeout)
        await upgrade(engine)
        app.state.engine = engine
        app.state.sessions = make_sessionmaker(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Knowledge Management", version="5.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.operations = load_operations(settings.oas_path)
    app.state.token_validator = token_validator or (TokenValidator(settings) if settings.auth_enabled else None)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        media_type = "application/problem+json" if "application/problem+json" in request.headers.get("accept", "") \
            else "application/json"
        return JSONResponse(exc.body(request.url.path), status_code=exc.status_code, headers=exc.headers,
                            media_type=media_type)

    @app.get("/health", include_in_schema=False)
    async def health() -> JSONResponse:
        checks = await run_checks(settings)
        body = {"status": "ready" if is_ready(checks) else "degraded",
                "checks": {name: check.as_dict() for name, check in checks.items()}}
        return JSONResponse(body, status_code=200)

    @app.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        checks = await run_checks(settings)
        ok = is_ready(checks)
        body = {"status": "ready" if ok else "not-ready",
                "checks": {name: check.as_dict() for name, check in checks.items()}}
        return JSONResponse(body, status_code=200 if ok else 503)

    # The TMFxxx resources are added to this router by the following issues.
    api = APIRouter(prefix=settings.base_path)
    app.include_router(api)
    app.state.api_router = api
    return app

