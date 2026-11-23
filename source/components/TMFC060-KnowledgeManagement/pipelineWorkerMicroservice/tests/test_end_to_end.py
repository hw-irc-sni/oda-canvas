"""End to end: a runbook document and a TMF921 intent through KM-FX-01..06 into the graph, Qdrant and search.

The real API (the dev path dependency) and the real worker with the default handlers run in-process, with Postgres
and Qdrant in containers. Three OpenAI-compatible stub model servers run on real sockets, one per model route: the
ModelGatewayService route (found through a fake Kubernetes API that serves the CRD and a CR naming the stub), the
agentgateway route and the plain OpenAI-compatible route. The stubs answer deterministically (canned extraction per
prompt text, embeddings from keywords), so the test asserts exact triples, hits and routes. The fixtures in
``fixtures/`` are written for this test.
"""
import json
import math
import socket
import threading
import time
from collections import Counter
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from qdrant_client import QdrantClient

from worker.steps import default_handlers

from .conftest import running_api
from .test_worker import make_worker, process, wait_done

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"
AS = "https://example.org/e2e/assurance#"
ICM = "http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/"
HB = "https://example.org/e2e/intent/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
PROV = "http://www.w3.org/ns/prov#"
INTENT = json.loads((FIXTURES / "harbour-intent.json").read_text())
RUNBOOK = (FIXTURES / "harbour-runbook.md").read_bytes()
CONCEPTS = {"outage": 0, "down": 0, "power": 0, "alarm": 1, "rectifier": 1, "latency": 2, "intent": 2}
DIMENSION = 4


def entity(kb: str, cls: str, slug: str) -> str:
    return f"urn:tmfc060:kb:{kb}:entity:{cls}:{slug}"


# ---- stub model servers ---------------------------------------------------------------------------------------
def embedding(text: str) -> list[float]:
    v = [0.05] * DIMENSION
    for word in text.lower().replace(".", " ").replace(",", " ").split():
        if word in CONCEPTS:
            v[CONCEPTS[word]] += 1.0
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def extraction(text: str) -> dict:
    """The entities a careful model would find in a chunk of the runbook, with the ontology's terms."""
    entities, relations, attributes = [], [], []
    if "4711" in text:
        entities.append({"id": "c", "type": AS + "Cell", "label": "Cell 4711"})
    if "Harbour-3" in text:
        entities.append({"id": "s", "type": AS + "Site", "label": "Harbour-3"})
        if "4711" in text:
            relations.append({"subject": "c", "predicate": AS + "locatedAt", "object": "s"})
    if "mains alarm" in text:
        entities.append({"id": "a", "type": AS + "Alarm", "label": "Mains alarm"})
        if "4711" in text:
            relations.append({"subject": "a", "predicate": AS + "affects", "object": "c"})
        if "unknown" in text:
            attributes.append({"subject": "a", "predicate": AS + "severity", "value": "unknown"})
    return {"entities": entities, "relations": relations, "attributes": attributes}


class Stub:
    """An OpenAI-compatible model server: every call is logged; ``up`` False answers 503 to everything, and
    ``fail_after`` n answers 503 from the (n+1)th embedding or chat call on."""

    def __init__(self, name: str):
        self.name, self.up, self.fail_after = name, True, None
        self.calls: Counter = Counter()
        app = FastAPI()

        def down(kind: str) -> bool:
            if not self.up:
                return True
            if kind != "models" and self.fail_after is not None and self.served() >= self.fail_after:
                return True
            return False

        @app.get("/v1/models")
        async def models():
            self.calls["models"] += 1
            if down("models"):
                return JSONResponse({"error": "down"}, status_code=503)
            return {"data": [{"id": "chat"}, {"id": "embed"}]}

        @app.post("/v1/embeddings")
        async def embeddings(request: Request):
            if down("embeddings"):
                return JSONResponse({"error": "down"}, status_code=503)
            self.calls["embeddings"] += 1
            texts = (await request.json())["input"]
            return {"data": [{"index": i, "embedding": embedding(t)} for i, t in enumerate(texts)]}

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            if down("chat"):
                return JSONResponse({"error": "down"}, status_code=503)
            self.calls["chat"] += 1
            messages = (await request.json())["messages"]
            if messages[0]["content"].startswith("A knowledge-base ingestion found a conflict"):
                answer = {"action": "edit", "rationale": "Ask the field team for the severity, then record it."}
            else:
                answer = extraction(messages[-1]["content"])
            return {"choices": [{"message": {"role": "assistant", "content": json.dumps(answer)}}]}

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True)
        self.thread.start()
        while not self.server.started:
            time.sleep(0.02)

    def served(self) -> int:
        return self.calls["embeddings"] + self.calls["chat"]

    def reset(self) -> None:
        self.up, self.fail_after = True, None
        self.calls.clear()

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)


def fake_kubernetes(mgs: Stub) -> httpx.MockTransport:
    """The Kubernetes API, as the resolver reads it: the ModelGatewayService CRD and the CR ``models/llm``."""
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/modelgatewayservices.oda.tmforum.org":
            return httpx.Response(200, json={"kind": "CustomResourceDefinition"})
        if path == "/apis/oda.tmforum.org/v1alpha1/namespaces/models/modelgatewayservices/llm":
            return httpx.Response(200, json={"kind": "ModelGatewayService", "metadata": {"name": "llm"},
                                             "spec": {"models": [{"name": "chat"}, {"name": "embed"}]},
                                             "status": {"gatewayEndpoint": mgs.url}})
        return httpx.Response(404, json={"kind": "Status", "code": 404})
    return httpx.MockTransport(handle)


# ---- the stack ----------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def stubs():
    made = {name: Stub(name) for name in ("modelGatewayService", "agentgateway", "openaiCompatible")}
    yield made
    for stub in made.values():
        stub.stop()


@pytest.fixture
async def e2e(postgres_url, qdrant_url, tmp_path, stubs):
    from app.models import K8s, ModelResolver
    qdrant = QdrantClient(url=qdrant_url)
    for c in qdrant.get_collections().collections:
        qdrant.delete_collection(c.name)
    for stub in stubs.values():
        stub.reset()
    async with running_api(postgres_url, tmp_path, qdrant_url, embedding_dimension=DIMENSION) as api:
        state = api.app.state
        await state.models.aclose()
        state.models = ModelResolver(state.settings.model_copy(update={
            "model_gateway_service": "models/llm", "mgs_chat_model": "chat", "mgs_embedding_model": "embed",
            "agentgateway_url": f"{stubs['agentgateway'].url}/v1", "agentgateway_chat_model": "chat",
            "agentgateway_embedding_model": "embed",
            "openai_base_url": f"{stubs['openaiCompatible'].url}/v1", "openai_chat_model": "chat",
            "openai_embedding_model": "embed"}),
            k8s=K8s("http://kubernetes.test", "token", transport=fake_kubernetes(stubs["modelGatewayService"])),
            use_in_cluster=False)
        await state.models.resolve()
        spec = f"{api.base}/knowledgeSpecification"
        for mid, kind, fixture, iri in (
                ("e2e-as", "ontology", "harbour-ontology.ttl", "https://example.org/e2e/assurance"),
                ("e2e-shapes", "shapes", "harbour-shapes.ttl", "https://example.org/e2e/assurance-shapes")):
            r = await api.post(spec, json={"id": mid, "name": mid, "iri": iri,
                                           "layer": "Telecom", "specKind": kind, "source": {"kind": "upload"}})
            assert r.status_code == 201, r.text
            r = await api.put(f"{spec}/{mid}/content", content=(FIXTURES / fixture).read_bytes(),
                              headers={"Content-Type": "text/turtle"})
            assert r.status_code < 300, r.text
            for status in ("proposed", "inReview", "agreed", "published"):
                r = await api.patch(f"{spec}/{mid}", json={"lifecycleStatus": status})
                assert r.status_code == 200, r.text
        api.qdrant, api.stubs = qdrant, stubs
        yield api


async def make_kb(api, kb: str) -> None:
    r = await api.post(f"{api.base}/knowledge", json={
        "id": kb, "name": kb, "storeKind": "hybrid", "conformsTo": [{"id": "e2e-as"}, {"id": "e2e-shapes"}],
        "source": [{"kind": "upload"}, {"kind": "tmf921"}]})
    assert r.status_code == 201, r.text


async def run_ingest(api, kb: str, body: dict) -> dict:
    r = await api.post(f"{api.base}/knowledge/{kb}/ingest", json=body)
    assert r.status_code == 201, r.text
    worker = make_worker(api, "e2e")
    worker.handlers = default_handlers(worker.settings)
    worker.start()
    try:
        done = await wait_done(api, r.json()["id"], timeout=120)
    finally:
        await worker.stop()
    assert done["state"] == "completed", json.dumps(done)[:2000]
    return done


async def ingest_runbook(api, kb: str) -> tuple[str, dict]:
    r = await api.post(f"{api.base}/knowledge/{kb}/document",
                       files={"file": ("harbour-runbook.md", RUNBOOK, "text/markdown")})
    assert r.status_code == 201, r.text
    doc = r.json()["id"]
    return doc, await run_ingest(api, kb, {"source": "upload", "documentId": [doc]})


async def ask(api, kb: str, sparql: str) -> dict:
    r = await api.post(f"{api.base}/knowledge/{kb}/query", content=sparql.encode(),
                       headers={"Content-Type": "application/sparql-query"})
    assert r.status_code == 200, r.text
    return r.json()


def served(stubs) -> dict:
    return {name: stub.served() for name, stub in stubs.items()}


# ---- the pipeline -------------------------------------------------------------------------------------------------
async def test_document_and_intent_reach_graph_vectors_and_search(e2e):
    api, kb = e2e, "harbour"
    await make_kb(api, kb)
    doc, doc_process = await ingest_runbook(api, kb)
    intent_process = await run_ingest(api, kb, {"source": "tmf921", "params": {"resources": [INTENT]}})

    # TMF701: both processes and every task of them completed
    for p in (doc_process, intent_process):
        refs = (await process(api, p["id"]))["task"]
        tasks = [(await api.get(f"/km/tmf-api/process/v5/task/{t['id']}")).json() for t in refs]
        assert {t["state"] for t in tasks} == {"completed"}, tasks
        assert [t["step"] for t in tasks] == [f"KM-FX-0{n}" for n in range(1, 7)], tasks

    cell, site, alarm = entity(kb, "Cell", "cell-4711"), entity(kb, "Site", "harbour-3"), \
        entity(kb, "Alarm", "mains-alarm")
    # KM-FX-02 extraction and KM-FX-05 storage: the runbook's entities and relations
    for s, p, o in ((alarm, AS + "affects", cell), (cell, AS + "locatedAt", site), (cell, RDF_TYPE, AS + "Cell"),
                    (alarm, RDF_TYPE, AS + "Alarm")):
        assert (await ask(api, kb, f"ASK {{ <{s}> <{p}> <{o}> }}"))["boolean"], (s, p, o)
    # KM-FX-04: OWL-RL inference (a Cell is a Site), and the out-of-list severity withheld
    assert (await ask(api, kb, f"ASK {{ <{cell}> a <{AS}Site> }}"))["boolean"]
    assert not (await ask(api, kb, f"ASK {{ <{alarm}> <{AS}severity> ?v }}"))["boolean"]
    # KM-FX-02 direct lifting of the TMF921 intent's expression, in the Intent Common Model
    for s, p, o in ((HB + "INT-HARB-7", RDF_TYPE, ICM + "Intent"),
                    (HB + "INT-HARB-7", ICM + "hasExpectation", HB + "INT-HARB-7-latency"),
                    (HB + "INT-HARB-7-latency", ICM + "target", HB + "HarbourDistrict")):
        assert (await ask(api, kb, f"ASK {{ <{s}> <{p}> <{o}> }}"))["boolean"], (s, p, o)
    # KM-FX-03 provenance: the entities were derived from the document
    rows = (await ask(api, kb, f"SELECT ?src WHERE {{ <{alarm}> <{PROV}wasDerivedFrom> ?src }}"))["results"]
    assert rows["bindings"], "no provenance for the alarm"

    # KM-FX-04 proposal: the withheld severity, with the model's suggestion
    proposals = (await api.get(f"{api.base}/knowledge/{kb}/proposal")).json()
    conflicts = [p for p in proposals if p["kind"] == "dataConflict"]
    assert conflicts, proposals
    severity = [p for p in conflicts if '"unknown"' in json.dumps(p)]
    assert severity and "Ask the field team for the severity" in json.dumps(severity[0]), conflicts

    # KM-FX-05: chunks in Qdrant, tagged with entity IRIs; the intent's chunk with the intent's IRI
    points = api.qdrant.scroll(f"tmfc060-kb-{kb}", limit=100, with_payload=True)[0]
    by_source = {}
    for point in points:
        by_source.setdefault(point.payload["source"], []).append(point.payload)
    assert doc in by_source and len(by_source) == 2, by_source.keys()
    doc_iris = {i for payload in by_source[doc] for i in payload["entityIri"]}
    assert {cell, site, alarm} <= doc_iris
    intent_payloads = next(v for k, v in by_source.items() if k != doc)
    assert intent_payloads[0]["entityIri"] == [INTENT["href"]]

    # hybrid search: IRI-tagged hits, and an IRI filter by class (with subclasses)
    r = await api.post(f"{api.base}/knowledge/{kb}/search", json={"query": "cell outage power alarm", "topK": 5})
    assert r.status_code == 200 and r.headers.get("X-Search-Mode") == "hybrid", (r.status_code, r.headers)
    hits = r.json()["hits"]
    assert hits[0]["documentId"] == doc and {cell, alarm} <= set(hits[0]["entityIri"])
    r = await api.post(f"{api.base}/knowledge/{kb}/search", json={"query": "latency intent", "topK": 5})
    assert INTENT["href"] in {i for h in r.json()["hits"] for i in h["entityIri"]}
    r = await api.post(f"{api.base}/knowledge/{kb}/search", json={"query": "outage", "iriFilter": [AS + "Site"]})
    assert r.json()["hits"] and all({cell, site} & set(h["entityIri"]) for h in r.json()["hits"])

    # KM-FX-06 and /recommend: the alarm is recommended for the cell
    r = await api.post(f"{api.base}/knowledge/{kb}/recommend", json={"context": "outage", "iri": [cell], "topK": 5})
    assert r.status_code == 200 and alarm in {i["itemId"] for i in r.json()["items"]}, r.text

    stats = (await api.get(f"{api.base}/knowledge/{kb}")).json()["statistics"]
    assert stats["documents"] == 1 and stats["chunks"] == len(points) and stats["triples"] > 0
    # with every route up, the ModelGatewayService route served every model call
    assert served(api.stubs) == {"modelGatewayService": served(api.stubs)["modelGatewayService"],
                                 "agentgateway": 0, "openaiCompatible": 0}
    assert served(api.stubs)["modelGatewayService"] > 0


# ---- the resolver, route by route ----------------------------------------------------------------------------------
@pytest.mark.parametrize("down, expected", [
    ((), "modelGatewayService"),
    (("modelGatewayService",), "agentgateway"),
    (("modelGatewayService", "agentgateway"), "openaiCompatible"),
])
async def test_the_resolver_uses_the_first_route_that_is_up(e2e, down, expected):
    api, kb = e2e, "routes"
    for name in down:
        api.stubs[name].up = False
    await api.app.state.models.resolve()
    status = api.app.state.models.status()
    assert status["route"] == expected and status["embeddingDimension"] == DIMENSION, status
    for stub in api.stubs.values():
        stub.calls.clear()
    await make_kb(api, kb)
    await ingest_runbook(api, kb)
    calls = served(api.stubs)
    assert calls[expected] > 0 and all(n == 0 for name, n in calls.items() if name != expected), calls
    hits = (await api.post(f"{api.base}/knowledge/{kb}/search", json={"query": "cell outage"})).json()["hits"]
    assert entity(kb, "Cell", "cell-4711") in hits[0]["entityIri"]


async def test_a_failing_route_fails_over_during_an_ingest(e2e):
    api, kb = e2e, "failover"
    mgs, agw = api.stubs["modelGatewayService"], api.stubs["agentgateway"]
    assert api.app.state.models.status()["route"] == "modelGatewayService"
    mgs.calls.clear()
    mgs.fail_after = 1  # one model call succeeds, then the gateway answers 503
    await make_kb(api, kb)
    await ingest_runbook(api, kb)
    assert mgs.served() == 1 and agw.served() > 0 and api.stubs["openaiCompatible"].served() == 0
    status = api.app.state.models.status()
    assert status["route"] == "agentgateway"
    assert {r["route"]: r["status"] for r in status["routes"]}["modelGatewayService"] == "failed", status
    assert (await ask(api, kb, f"ASK {{ <{entity(kb, 'Alarm', 'mains-alarm')}> <{AS}affects> ?c }}"))["boolean"]
