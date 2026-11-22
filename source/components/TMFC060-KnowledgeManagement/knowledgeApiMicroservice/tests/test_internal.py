"""The internal API of the pipeline workers: token, graph writes by source, task calls, and its absence publicly."""
import os
import time
from pathlib import Path

import httpx
import pytest
import yaml
from rdflib import Graph

from app.config import Settings

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR = "knowledge-editor"
A = b"<urn:x:a> <urn:x:p> <urn:x:1> ."
B = b"<urn:x:b> <urn:x:p> <urn:x:2> ."
B2 = b"<urn:x:b> <urn:x:p> <urn:x:3> ."
MAIN = b"<urn:x:m> <urn:x:p> <urn:x:0> ."


@pytest.fixture
async def api(postgres_url, tmp_path):
    token = tmp_path / "token"
    token.write_text("s3cret-token\n")
    async with running_api(postgres_url, str(tmp_path / "store"), internal_token_file=str(token)) as client:
        client.token_file = token
        client.internal = httpx.AsyncClient(base_url=f"http://127.0.0.1:{client.app.state.internal.port}")
        r = await client.post(f"{client.base}/knowledge", json={"id": "kb", "name": "KB", "storeKind": "graph"},
                              headers=auth(EDITOR))
        assert r.status_code == 201, r.text
        yield client
        await client.internal.aclose()


TOKEN = {"Authorization": "Bearer s3cret-token"}
TURTLE = {"Content-Type": "text/turtle"}


async def public_graph(api):
    r = await api.get(f"{api.base}/knowledge/kb/graph", headers=auth())
    return {tuple(str(x) for x in t) for t in Graph().parse(data=r.content, format="turtle")}


async def test_a_valid_token_writes_by_source(api):
    i = api.internal
    assert (await i.post("/internal/knowledge/kb/graph", content=MAIN, headers={**TOKEN, **TURTLE})).status_code == 204
    assert (await i.put("/internal/knowledge/kb/graph", params={"source": "doc-a", "task": "tk-1"}, content=A,
                        headers={**TOKEN, **TURTLE})).status_code == 204
    await i.put("/internal/knowledge/kb/graph", params={"source": "doc-b"}, content=B, headers={**TOKEN, **TURTLE})
    assert len(await public_graph(api)) == 3  # the public view is the union
    # re-ingesting doc-b replaces only what doc-b contributed
    await i.put("/internal/knowledge/kb/graph", params={"source": "doc-b"}, content=B2, headers={**TOKEN, **TURTLE})
    graph = await public_graph(api)
    assert ("urn:x:b", "urn:x:p", "urn:x:3") in graph and ("urn:x:b", "urn:x:p", "urn:x:2") not in graph
    assert ("urn:x:a", "urn:x:p", "urn:x:1") in graph and len(graph) == 3
    stats = (await api.get(f"{api.base}/knowledge/kb", headers=auth())).json()["statistics"]
    assert stats["triples"] == 3
    q = await api.post(f"{api.base}/knowledge/kb/query", content=b"SELECT ?o WHERE { <urn:x:a> ?p ?o }",
                       headers={**auth(), "Content-Type": "application/sparql-query"})
    assert q.json()["results"]["bindings"][0]["o"]["value"] == "urn:x:1"  # queries see the source graphs
    store = api.app.state.graphs.store
    assert store.query("""ASK { GRAPH <urn:tmfc060:governance> { ?w a <http://www.w3.org/ns/prov#Activity> ;
        <http://www.w3.org/ns/prov#generated> <urn:tmfc060:kb:kb:source:doc-a> ;
        <http://www.w3.org/ns/prov#wasInformedBy> <urn:tmfc060:task:tk-1> } }""")
    # the public PUT and DELETE act on the whole knowledge-base
    await api.put(f"{api.base}/knowledge/kb/graph", content=MAIN, headers={**auth(EDITOR), **TURTLE})
    assert len(await public_graph(api)) == 1
    await i.put("/internal/knowledge/kb/graph", params={"source": "doc-a"}, content=A, headers={**TOKEN, **TURTLE})
    await api.delete(f"{api.base}/knowledge/kb/graph", headers=auth(EDITOR))
    assert await public_graph(api) == set()


async def test_bad_requests(api):
    i = api.internal
    assert (await i.put("/internal/knowledge/kb/graph", content=A, headers={**TOKEN, **TURTLE})).status_code == 400
    assert (await i.put("/internal/knowledge/kb/graph", params={"source": "../x"}, content=A,
                        headers={**TOKEN, **TURTLE})).status_code == 400
    assert (await i.post("/internal/knowledge/kb/graph", content=b"nope <", headers={**TOKEN, **TURTLE})).status_code == 400
    assert (await i.post("/internal/knowledge/none/graph", content=A, headers={**TOKEN, **TURTLE})).status_code == 404


async def test_missing_or_wrong_token(api):
    i = api.internal
    for headers in [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "s3cret-token"}]:
        r = await i.post("/internal/knowledge/kb/graph", content=A, headers={**headers, **TURTLE})
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert (await i.post("/internal/task/claim", json={"worker": "w"}, headers=headers)).status_code == 401


async def test_the_token_is_rotated_without_restart(api):
    api.token_file.write_text("rotated")
    os.utime(api.token_file, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    i = api.internal
    assert (await i.post("/internal/task/claim", json={"worker": "w"}, headers=TOKEN)).status_code == 401
    assert (await i.post("/internal/task/claim", json={"worker": "w"},
                         headers={"Authorization": "Bearer rotated"})).status_code == 204


async def test_task_calls(api):
    i = api.internal
    up = await api.post(f"{api.base}/knowledge/kb/document", files={"file": ("a.md", b"# a", "text/markdown")},
                        headers=auth(EDITOR))
    await api.post(f"{api.base}/knowledge/kb/ingest", json={}, headers=auth(EDITOR))
    claimed = await i.post("/internal/task/claim", json={"worker": "w1"}, headers=TOKEN)
    assert claimed.status_code == 200
    task = claimed.json()
    assert task["step"] == "KM-FX-01" and task["payload"]["documentId"] == up.json()["id"] and task["attempt"] == 1
    assert (await i.post(f"/internal/task/{task['id']}/heartbeat", json={"worker": "w1"}, headers=TOKEN)).status_code == 204
    assert (await i.post(f"/internal/task/{task['id']}/complete", json={"worker": "w2"},
                         headers=TOKEN)).status_code == 409  # not w2's task
    assert (await i.post(f"/internal/task/{task['id']}/complete", json={"worker": "w1", "result": {"chunks": 1}},
                         headers=TOKEN)).status_code == 204
    second = (await i.post("/internal/task/claim", json={"worker": "w1"}, headers=TOKEN)).json()
    assert second["step"] == "KM-FX-02"
    assert (await i.post(f"/internal/task/{second['id']}/fail", json={"worker": "w1", "error": "x", "retryable": False},
                         headers=TOKEN)).status_code == 204
    assert (await i.post("/internal/task/claim", json={"worker": "w1"}, headers=TOKEN)).status_code == 204
    assert (await i.post("/internal/task/claim", json={}, headers=TOKEN)).status_code == 400


async def test_absent_from_the_public_api(api):
    for path in ["/internal/task/claim", f"{api.base}/internal/task/claim", "/internal/knowledge/kb/graph"]:
        r = await api.post(path, json={"worker": "w"}, headers=TOKEN)
        assert r.status_code == 404, path
    settings = Settings()
    for oas in (settings.oas_path, settings.process_oas_path):
        assert not any("internal" in p for p in yaml.safe_load(Path(oas).read_text())["paths"])


async def test_no_token_no_listener(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), internal_token_file=str(tmp_path / "missing")) as api:
        assert api.app.state.internal.server is None and api.app.state.internal.port is None


async def test_document_content_claim_input_and_result_cap(postgres_url, tmp_path):
    token = tmp_path / "token"
    token.write_text("s3cret-token")
    async with running_api(postgres_url, str(tmp_path / "store"), internal_token_file=str(token),
                           task_result_max_bytes=1000) as api:
        i = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api.app.state.internal.port}")
        await api.post(f"{api.base}/knowledge", json={"id": "kb", "name": "KB", "storeKind": "graph"},
                       headers=auth(EDITOR))
        content = b"# Title\n\n" + b"x" * 3000
        doc = (await api.post(f"{api.base}/knowledge/kb/document", files={"file": ("a.md", content, "text/markdown")},
                              headers=auth(EDITOR))).json()
        meta = await i.get(f"/internal/knowledge/kb/document/{doc['id']}", headers=TOKEN)
        assert meta.json()["mediaType"] == "text/markdown" and meta.json()["size"] == len(content)
        got = await i.get(f"/internal/knowledge/kb/document/{doc['id']}/content", headers=TOKEN)
        assert got.content == content and got.headers["content-type"].startswith("text/markdown")
        assert (await i.get(f"/internal/knowledge/kb/document/{doc['id']}/content")).status_code == 401
        assert (await i.get("/internal/knowledge/kb/document/doc-x/content", headers=TOKEN)).status_code == 404
        await api.post(f"{api.base}/knowledge/kb/ingest", json={}, headers=auth(EDITOR))
        first = (await i.post("/internal/task/claim", json={"worker": "w"}, headers=TOKEN)).json()
        assert first["input"] is None
        too_big = await i.post(f"/internal/task/{first['id']}/complete", json={"worker": "w", "result": {"t": "y" * 2000}},
                               headers=TOKEN)
        assert too_big.status_code == 413
        await i.post(f"/internal/task/{first['id']}/complete", json={"worker": "w", "result": {"chunks": 3}},
                     headers=TOKEN)
        second = (await i.post("/internal/task/claim", json={"worker": "w"}, headers=TOKEN)).json()
        assert second["step"] == "KM-FX-02" and second["input"] == {"chunks": 3}
        await i.aclose()


async def test_models_and_query(api):
    import json as _json
    from app.models import ModelResolver
    i = api.internal
    # no model route
    assert (await i.post("/internal/models/chat", json={"messages": [{"role": "user", "content": "hi"}]},
                         headers=TOKEN)).status_code == 503

    def fake(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        body = _json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": n, "embedding": [1.0, 0.0]}
                                                      for n, _ in enumerate(body["input"])]})
        if body.get("response_format") == {"type": "bad"}:
            return httpx.Response(400, json={"error": "bad"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
    state = api.app.state
    state.models = ModelResolver(state.settings.model_copy(update={
        "openai_base_url": "http://m.test/v1", "openai_chat_model": "c", "openai_embedding_model": "e"}),
        transport=httpx.MockTransport(fake), use_in_cluster=False)
    await state.models.resolve()
    chat = await i.post("/internal/models/chat", json={"messages": [{"role": "user", "content": "hi"}],
                                                        "response_format": {"type": "json_object"}}, headers=TOKEN)
    assert chat.status_code == 200 and chat.json() == {"content": '{"ok": true}', "route": "openaiCompatible"}
    assert (await i.post("/internal/models/chat", json={"messages": [{"role": "user", "content": "x"}],
                                                        "response_format": {"type": "bad"}},
                         headers=TOKEN)).status_code == 400
    emb = await i.post("/internal/models/embeddings", json={"input": ["a", "b"]}, headers=TOKEN)
    assert emb.json()["embeddings"] == [[1.0, 0.0], [1.0, 0.0]]
    assert (await i.post("/internal/models/chat", json={}, headers=TOKEN)).status_code == 400
    assert (await i.post("/internal/models/embeddings", json={"input": "x"}, headers=TOKEN)).status_code == 400
    assert (await i.post("/internal/models/embeddings", json={"input": ["a"]})).status_code == 401
    # read-only SPARQL over the KB's dataset
    await i.post("/internal/knowledge/kb/graph", content=MAIN, headers={**TOKEN, **TURTLE})
    q = await i.post("/internal/knowledge/kb/query", content=b"SELECT ?o WHERE { <urn:x:m> ?p ?o }", headers=TOKEN)
    assert q.json()["results"]["bindings"][0]["o"]["value"] == "urn:x:0"
    assert (await i.post("/internal/knowledge/kb/query", content=b"INSERT DATA { <urn:a> <urn:b> <urn:c> }",
                         headers=TOKEN)).status_code == 400
    svc = await i.post("/internal/knowledge/kb/query", content=b"SELECT * WHERE { SERVICE <https://x/sparql> { ?s ?p ?o } }",
                       headers=TOKEN)
    assert svc.status_code == 403
    assert (await i.post("/internal/knowledge/none/query", content=b"ASK {}", headers=TOKEN)).status_code == 404
