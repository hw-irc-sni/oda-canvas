"""/health (the loop is alive) and /ready (the internal API answers with the token) for Kubernetes probes."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .loop import Worker


def health_app(worker: Worker) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        alive = bool(worker.slots) and not all(s.done() for s in worker.slots)
        body = {"status": "up" if alive else "down", "inFlight": sorted(worker.in_flight),
                "lastClaim": worker.last_claim, "worker": worker.client.worker}
        return JSONResponse(body, status_code=200 if alive else 503)

    @app.get("/ready")
    async def ready():
        ok = await worker.client.ready()
        return JSONResponse({"status": "ready" if ok else "not-ready"}, status_code=200 if ok else 503)

    return app
