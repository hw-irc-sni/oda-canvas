"""The internal API of the pipeline workers, on a port of its own (``INTERNAL_PORT``) that no ExposedAPI references.

The worker reaches the component only through this API: it has no database, Qdrant or model access of its own.
Every call needs ``Authorization: Bearer <token>`` with the shared service token read from ``INTERNAL_TOKEN_FILE``
(re-read when the file changes, so a rotated Secret takes effect without a restart; compared in constant time).
Without a token the listener is not started.

- ``POST /internal/knowledge/{id}/graph`` merges triples into the knowledge-base graph;
  ``PUT /internal/knowledge/{id}/graph?source=<src>`` replaces what that source (a document or a batch) contributed,
  kept in ``urn:tmfc060:kb:{id}:source:{src}``. Each write is recorded as a ``prov:Activity`` in the governance graph.
- ``POST /internal/task/claim``, ``/internal/task/{id}/heartbeat``, ``/complete`` and ``/fail``: the task calls of
  ``processes.py`` (204 when no task is due; 409 when the lease is lost).
"""
import asyncio
import hmac
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from pyoxigraph import Literal, NamedNode, Quad

import httpx

from . import processes
from .models import ModelError
from .query import run_query
from .db.models import Document, DocumentBlob, Task
from .db.models import Knowledge as KnowledgeRow
from .db.models import KnowledgeSpecification as SpecRow
from .errors import ApiError
from .events import Event
from .graphstore import GOVERNANCE_GRAPH, kb_graph, source_graph

log = logging.getLogger(__name__)
SOURCE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
PROV = "http://www.w3.org/ns/prov#"
RDF_TYPE = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
XSD_DATETIME = NamedNode("http://www.w3.org/2001/XMLSchema#dateTime")


class InternalToken:
    """The shared service token, re-read when its file changes."""

    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self.mtime: float | None = None
        self.value = ""

    def current(self) -> str:
        if self.path is None:
            return ""
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            self.mtime, self.value = None, ""
            return ""
        if mtime != self.mtime:
            self.mtime, self.value = mtime, self.path.read_text().strip()
        return self.value

    def check(self, header: str | None) -> bool:
        token = self.current()
        if not token or not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer "):].encode(), token.encode())


def unauthorized() -> ApiError:
    return ApiError(401, "401", "Unauthorized", "A valid internal service token is required",
                    headers={"WWW-Authenticate": "Bearer"})


def build_app(main: FastAPI, token: InternalToken) -> FastAPI:
    state = main.state
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    async def authorized(request: Request) -> None:
        if not token.check(request.headers.get("authorization")):
            raise unauthorized()

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.body(request.url.path), status_code=exc.status_code, headers=exc.headers)

    async def writable_kb(session, kb_id: str) -> KnowledgeRow:
        row = await session.get(KnowledgeRow, kb_id)
        if row is None:
            raise ApiError(404, "404", "Not Found", f"No knowledge '{kb_id}'")
        if row.lifecycle_status == "deprecated":
            raise ApiError(409, "409", "Conflict", "A deprecated knowledge-base cannot be changed")
        return row

    def record_write(graph: str, task: str | None) -> None:
        store = state.graphs.store
        activity = NamedNode(f"urn:tmfc060:write:{uuid.uuid4()}")
        governance = NamedNode(GOVERNANCE_GRAPH)
        quads = [(RDF_TYPE, NamedNode(PROV + "Activity")), (NamedNode(PROV + "generated"), NamedNode(graph)),
                 (NamedNode(PROV + "endedAtTime"),
                  Literal(datetime.now(timezone.utc).isoformat(), datatype=XSD_DATETIME)),
                 (NamedNode(PROV + "wasAssociatedWith"), NamedNode("urn:tmfc060:service:pipelineWorker"))]
        if task:
            quads.append((NamedNode(PROV + "wasInformedBy"), NamedNode(f"urn:tmfc060:task:{task}")))
        for predicate, obj in quads:
            store.add(Quad(activity, predicate, obj, governance))

    async def write(request: Request, kb_id: str, source: str | None):
        media_type = request.headers.get("content-type", "text/turtle").split(";")[0].strip()
        task = request.query_params.get("task")
        async with state.sessions() as session:
            row = await writable_kb(session, kb_id)
            data = await request.body()
            if source is None:
                graph = kb_graph(kb_id)
                state.graphs.merge(graph, data, media_type)
            else:
                if not SOURCE.match(source):
                    raise ApiError(400, "400", "Bad Request", "source must be 1-128 letters, digits, '.', '_' or '-'")
                graph = source_graph(kb_id, source)
                state.graphs.replace(graph, data, media_type)
            record_write(graph, task)
            body = row.body
        await state.events.publish(Event("KnowledgeAttributeValueChangeEvent", "knowledge", body))
        return Response(status_code=204)

    @app.post("/internal/knowledge/{kb_id}/graph", dependencies=[Depends(authorized)])
    async def merge_graph(kb_id: str, request: Request):
        return await write(request, kb_id, None)

    @app.put("/internal/knowledge/{kb_id}/graph", dependencies=[Depends(authorized)])
    async def replace_source_graph(kb_id: str, request: Request):
        source = request.query_params.get("source")
        if not source:
            raise ApiError(400, "400", "Bad Request", "PUT replaces one source's triples: give ?source=")
        return await write(request, kb_id, source)

    # ---- tasks (processes.py) ------------------------------------------------------------------------------
    async def body_of(request: Request) -> dict:
        try:
            payload = await request.json()
        except ValueError:
            raise ApiError(400, "400", "Bad Request", "The body must be JSON") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("worker"), str) or not payload["worker"]:
            raise ApiError(400, "400", "Bad Request", "worker is required")
        return payload

    def task_json(task, previous: dict | None) -> dict:
        return {"id": task.id, "processId": task.process_id, "step": task.step, "chain": task.chain,
                "attempt": task.attempts, "payload": task.payload, "input": previous,
                "leaseSeconds": state.settings.task_lease_seconds}

    @app.post("/internal/task/claim", dependencies=[Depends(authorized)])
    async def claim(request: Request):
        payload = await body_of(request)
        async with state.sessions() as session:
            task = await processes.claim(session, state.settings, payload["worker"])
            if task is None:
                return Response(status_code=204)
            previous = await session.scalar(select(Task.result).where(
                Task.process_id == task.process_id, Task.chain == task.chain, Task.seq == task.seq - 1))
        return JSONResponse(task_json(task, previous))

    # ---- models and SPARQL (KM-FX-02 onwards) --------------------------------------------------------------
    async def json_of(request: Request) -> dict:
        try:
            payload = await request.json()
        except ValueError:
            raise ApiError(400, "400", "Bad Request", "The body must be JSON") from None
        if not isinstance(payload, dict):
            raise ApiError(400, "400", "Bad Request", "The body must be a JSON object")
        return payload

    async def model_call(call):
        resolver = getattr(state, "models", None)
        try:
            if resolver is None:
                raise ModelError("No model resolver")
            return await call(resolver)
        except ModelError as exc:
            if exc.status is not None and 400 <= exc.status < 500:
                raise ApiError(400, "400", "Bad Request", str(exc)) from exc
            if exc.status is None and (resolver is None or resolver.active is None):
                raise ApiError(503, "503", "Service Unavailable", f"No model route: {exc}") from exc
            raise ApiError(502, "502", "Bad Gateway", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ApiError(502, "502", "Bad Gateway", f"{type(exc).__name__}: {exc}") from exc

    @app.post("/internal/models/chat", dependencies=[Depends(authorized)])
    async def chat(request: Request):
        payload = await json_of(request)
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ApiError(400, "400", "Bad Request", "messages is required")
        options = {k: v for k, v in payload.items() if k in ("response_format", "temperature", "max_tokens", "seed")}
        content = await model_call(lambda r: r.chat(messages, **options))
        return {"content": content, "route": state.models.active.name if state.models.active else None}

    @app.post("/internal/models/embeddings", dependencies=[Depends(authorized)])
    async def embeddings(request: Request):
        payload = await json_of(request)
        texts = payload.get("input")
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ApiError(400, "400", "Bad Request", "input must be a list of strings")
        vectors = await model_call(lambda r: r.embed(texts))
        return {"embeddings": vectors, "model": state.models.active.embedding_model if state.models.active else None}

    @app.post("/internal/knowledge/{kb_id}/query", dependencies=[Depends(authorized)])
    async def query(kb_id: str, request: Request):
        from .kb.router import dataset_for
        text = (await request.body()).decode("utf-8", errors="replace")
        if not text.strip():
            raise ApiError(400, "400", "Bad Request", "The query is empty")
        settings = state.settings
        async with state.sessions() as session:
            row = await session.get(KnowledgeRow, kb_id)
            if row is None:
                raise ApiError(404, "404", "Not Found", f"No knowledge '{kb_id}'")
            dataset = await dataset_for(session, state.graphs, row)
        result = await run_query(settings.store_path, text, dataset, timeout=settings.query_timeout_default,
                                 row_cap=settings.query_row_cap)  # no SERVICE: the allowlist is empty
        if result.kind == "graph":
            return Response(result.body, media_type="text/turtle")
        return JSONResponse(result.body, media_type="application/sparql-results+json",
                            headers={"X-Result-Truncated": "true"} if result.truncated else {})

    # ---- ontology and proposals (KM-FX-04) -----------------------------------------------------------------
    @app.get("/internal/knowledge/{kb_id}/ontology", dependencies=[Depends(authorized)])
    async def ontology(kb_id: str, request: Request):
        from .dataset import ModuleInfo, visible_modules
        from .graphstore import module_graph
        part = request.query_params.get("part", "ontology")
        if part not in ("ontology", "shapes"):
            raise ApiError(400, "400", "Bad Request", "part must be ontology or shapes")
        async with state.sessions() as session:
            row = await session.get(KnowledgeRow, kb_id)
            if row is None:
                raise ApiError(404, "404", "Not Found", f"No knowledge '{kb_id}'")
            specs = {m.id: m for m in (await session.scalars(select(SpecRow))).all()}
        infos = {i: ModuleInfo(m.lifecycle_status, tuple(r["id"] for r in m.body.get("imports") or []))
                 for i, m in specs.items()}
        visible = visible_modules([r["id"] for r in row.body.get("conformsTo") or []], infos)
        graphs = [module_graph(m) for m in visible if (specs[m].spec_kind == "shapes") == (part == "shapes")]
        return Response(state.graphs.export_union(graphs, "text/turtle"), media_type="text/turtle")

    @app.post("/internal/knowledge/{kb_id}/proposal", dependencies=[Depends(authorized)])
    async def propose(kb_id: str, request: Request):
        from .kb import proposals
        payload = await json_of(request)
        items = payload.get("proposals")
        if not isinstance(items, list):
            raise ApiError(400, "400", "Bad Request", "proposals must be a list")
        async with state.sessions() as session:
            await writable_kb(session, kb_id)
            ids = await proposals.record(session, kb_id, payload.get("source"), payload.get("processId"), items)
        return JSONResponse({"ids": ids}, status_code=201)

    # ---- documents (KM-FX-01) ------------------------------------------------------------------------------
    async def document_of(session, kb_id: str, doc_id: str) -> Document:
        doc = await session.get(Document, doc_id)
        if doc is None or doc.knowledge_id != kb_id:
            raise ApiError(404, "404", "Not Found", f"No document '{doc_id}' in '{kb_id}'")
        return doc

    @app.get("/internal/knowledge/{kb_id}/document/{doc_id}", dependencies=[Depends(authorized)])
    async def document(kb_id: str, doc_id: str):
        async with state.sessions() as session:
            doc = await document_of(session, kb_id, doc_id)
            return {"id": doc.id, "name": doc.name, "mediaType": doc.media_type, "size": doc.size,
                    "sha256": doc.sha256}

    @app.get("/internal/knowledge/{kb_id}/document/{doc_id}/content", dependencies=[Depends(authorized)])
    async def document_content(kb_id: str, doc_id: str):
        async with state.sessions() as session:
            doc = await document_of(session, kb_id, doc_id)
            media_type, size = doc.media_type, doc.size

        async def chunks():
            async with state.sessions() as session:
                seq = 0
                while (data := await session.scalar(select(DocumentBlob.data).where(
                        DocumentBlob.document_id == doc_id, DocumentBlob.seq == seq))) is not None:
                    yield data
                    seq += 1
        return StreamingResponse(chunks(), media_type=media_type, headers={"Content-Length": str(size)})

    async def held(call):
        try:
            async with state.sessions() as session:
                await call(session)
        except processes.LeaseLost:
            raise ApiError(409, "409", "Conflict", "The task is no longer held by this worker") from None
        return Response(status_code=204)

    @app.post("/internal/task/{task_id}/heartbeat", dependencies=[Depends(authorized)])
    async def heartbeat(task_id: str, request: Request):
        worker = (await body_of(request))["worker"]
        return await held(lambda s: processes.heartbeat(s, task_id, worker))

    @app.post("/internal/task/{task_id}/complete", dependencies=[Depends(authorized)])
    async def complete(task_id: str, request: Request):
        cap = state.settings.task_result_max_bytes
        if int(request.headers.get("content-length") or 0) > cap or len(await request.body()) > cap:
            raise ApiError(413, "413", "Payload Too Large", f"A task result may be at most {cap} bytes")
        payload = await body_of(request)
        result = payload.get("result") if isinstance(payload.get("result"), dict) else None
        return await held(lambda s: processes.complete(s, task_id, payload["worker"], result))

    @app.post("/internal/task/{task_id}/fail", dependencies=[Depends(authorized)])
    async def fail(task_id: str, request: Request):
        payload = await body_of(request)
        error = str(payload.get("error") or "failed")[:2000]
        retryable = payload.get("retryable", True) is not False
        return await held(lambda s: processes.fail(s, state.settings, task_id, payload["worker"], error, retryable))

    return app


class InternalServer:
    """Runs the internal app on INTERNAL_PORT during the main app's lifespan, if a token is configured."""

    def __init__(self, main: FastAPI):
        self.main = main
        self.token = InternalToken(main.state.settings.internal_token_file)
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task | None = None
        self.port: int | None = None

    async def start(self) -> None:
        if not self.token.current():
            log.warning("INTERNAL_TOKEN_FILE missing or empty: the internal API for the pipeline workers is off")
            return
        settings = self.main.state.settings
        config = uvicorn.Config(build_app(self.main, self.token), host=settings.internal_host,
                                port=settings.internal_port, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(config)
        self.server.install_signal_handlers = lambda: None
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            if self.task.done():
                self.task.result()
            await asyncio.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        log.info("internal API listening on %s:%s", settings.internal_host, self.port)

    async def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
            await self.task
