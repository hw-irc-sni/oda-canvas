"""FastAPI application of the TMFC060 knowledge API."""
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .health import is_ready, run_checks


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Knowledge Management", version="5.0.0")
    app.state.settings = settings

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
    return app


app = create_app()
