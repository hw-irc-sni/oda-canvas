"""Federation: SERVICE to published remote modules through the loopback relay, and everything else refused."""
import asyncio
import json
import socket
from urllib.parse import parse_qs

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from .api import auth, running_api

pytestmark = pytest.mark.integration

OXIGRAPH_IMAGE = "ghcr.io/oxigraph/oxigraph:0.5.3"
EDITOR, ONT_EDITOR, REVIEWER, APPROVER = "knowledge-editor", "ontology-editor", "ontology-reviewer", "ontology-approver"
PARTNER = b"""<https://partner.example.net/cell/4711> <https://partner.example.net/ont#vendor> "VendorA" .
<https://partner.example.net/cell/4712> <https://partner.example.net/ont#vendor> "VendorB" ."""
LOCAL = b"""<https://example.org/inc/1> <https://example.org/as#affects> <https://partner.example.net/cell/4711> ."""
JOIN = """SELECT ?inc ?vendor WHERE {{ ?inc <https://example.org/as#affects> ?cell .
  SERVICE <{url}> {{ ?cell <https://partner.example.net/ont#vendor> ?vendor }} }}"""


@pytest.fixture(scope="module")
def oxigraph():
    container = (DockerContainer(OXIGRAPH_IMAGE).with_command("serve --location /data --bind 0.0.0.0:7878")
                 .with_exposed_ports(7878).waiting_for(HttpWaitStrategy(7878, "/").for_status_code(200)))
    with container:
        base = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(7878)}"
        httpx.post(f"{base}/store?default", content=PARTNER, headers={"Content-Type": "text/turtle"}).raise_for_status()
        yield f"{base}/query"


class FakePeer:
    """A peer TMFC060 knowledge-base query URL protected by OAuth2 client credentials, plus a hanging endpoint."""

    def __init__(self):
        self.token_calls = self.query_calls = 0
        self.app = FastAPI()
        self.app.add_api_route("/token", self.token, methods=["POST"])
        self.app.add_api_route("/knowledge/kb/query", self.query, methods=["POST"])
        self.app.add_api_route("/hang", self.hang, methods=["POST"])

    async def token(self, request: Request):
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        if (form.get("grant_type"), form.get("client_id"), form.get("client_secret")) != (
                "client_credentials", "tmfc060-a", "s3cret"):
            return Response(status_code=401)
        self.token_calls += 1
        return {"access_token": "tok-1", "token_type": "Bearer", "expires_in": 300}

    async def query(self, request: Request):
        if request.headers.get("authorization") != "Bearer tok-1":
            return Response(status_code=401)
        self.query_calls += 1
        body = {"head": {"vars": ["cell", "vendor"]}, "results": {"bindings": [
            {"cell": {"type": "uri", "value": "https://partner.example.net/cell/4711"},
             "vendor": {"type": "literal", "value": "PeerVendor"}}]}}
        return Response(json.dumps(body), media_type="application/sparql-results+json")

    async def hang(self):
        await asyncio.sleep(10)
        return Response(status_code=200)


@pytest.fixture
async def peer():
    fake = FakePeer()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(fake.app, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.02)
    fake.base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    yield fake
    server.should_exit = True
    await task


@pytest.fixture
async def api(postgres_url, tmp_path, peer):
    secrets = tmp_path / "federation"
    for name, value in {"tokenUrl": f"{peer.base}/token", "clientId": "tmfc060-a", "clientSecret": "s3cret"}.items():
        (secrets / "peer-kb").mkdir(parents=True, exist_ok=True)
        (secrets / "peer-kb" / name).write_text(value)
    async with running_api(postgres_url, str(tmp_path / "store"), governance_require_validation=False,
                           federation_secrets_dir=str(secrets), federation_call_timeout=1.0) as client:
        r = await client.post(f"{client.base}/knowledge", json={"id": "local", "name": "Local", "storeKind": "graph"},
                              headers=auth(EDITOR))
        assert r.status_code == 201, r.text
        await client.put(f"{client.base}/knowledge/local/graph", content=LOCAL,
                         headers={**auth(EDITOR), "Content-Type": "text/turtle"})
        yield client


async def remote(api, mid, url, kind="sparql", publish=True):
    base = f"{api.base}/knowledgeSpecification"
    r = await api.post(base, json={"id": mid, "name": mid, "iri": f"https://partner.example.net/{mid}",
                                   "specKind": "remote", "source": {"kind": "remoteEndpoint", "url": url,
                                                                    "endpointKind": kind}}, headers=auth(ONT_EDITOR))
    assert r.status_code == 201, r.text
    if publish:
        for state, role in [("proposed", ONT_EDITOR), ("inReview", REVIEWER), ("agreed", APPROVER),
                            ("published", APPROVER)]:
            r = await api.patch(f"{base}/{mid}", json={"lifecycleStatus": state}, headers=auth(role))
            assert r.status_code == 200, r.text


async def query(api, text, **params):
    return await api.post(f"{api.base}/knowledge/local/query", content=text.encode(), params=params,
                          headers={**auth(), "Content-Type": "application/sparql-query"})


async def test_service_to_a_published_sparql_peer_joins_its_rows(api, oxigraph):
    await remote(api, "partner", oxigraph)
    r = await query(api, JOIN.format(url=oxigraph))
    assert r.status_code == 200, r.text
    assert [(b["inc"]["value"], b["vendor"]["value"]) for b in r.json()["results"]["bindings"]] == [
        ("https://example.org/inc/1", "VendorA")]


async def test_service_to_a_peer_tmfc060_uses_client_credentials_and_the_cache(api, peer):
    url = f"{peer.base}/knowledge/kb/query"
    await remote(api, "peer-kb", url, kind="tmfc060")
    for _ in range(3):
        r = await query(api, JOIN.format(url=url))
        assert r.status_code == 200, r.text
        assert r.json()["results"]["bindings"][0]["vendor"]["value"] == "PeerVendor"
    assert peer.token_calls == 1 and peer.query_calls == 1  # one token, then cached answers
    assert api.app.state.relay.calls == 1


async def test_everything_else_is_refused_before_any_remote_call(api, oxigraph, peer):
    await remote(api, "draft-peer", f"{peer.base}/knowledge/kb/query", publish=False)
    for target in ["https://elsewhere.example/sparql", f"{peer.base}/knowledge/kb/query"]:
        r = await query(api, JOIN.format(url=target))
        assert r.status_code == 403 and r.json()["code"] == "SERVICE_NOT_ALLOWED", r.text
    await remote(api, "partner", oxigraph)
    prefixed = f"PREFIX p: <{oxigraph.rsplit('/', 1)[0]}/> SELECT * WHERE {{ SERVICE p:query {{ ?s ?p ?o }} }}"
    r = await query(api, prefixed)
    assert r.status_code == 403 and "full IRIs" in r.json()["message"]
    r = await query(api, "SELECT * WHERE { SERVICE ?endpoint { ?s ?p ?o } }")
    assert r.status_code == 403
    assert peer.query_calls == 0 and api.app.state.relay.calls == 0


async def test_a_hanging_peer_gives_a_504_problem(api, peer):
    url = f"{peer.base}/hang"
    await remote(api, "hanging", url)
    r = await query(api, f"SELECT * WHERE {{ SERVICE <{url}> {{ ?s ?p ?o }} }}", timeout="30")
    assert r.status_code == 504 and r.json()["code"] == "FEDERATION_TIMEOUT", r.text
    silent = await query(api, f"SELECT * WHERE {{ SERVICE SILENT <{url}> {{ ?s ?p ?o }} }}")
    assert silent.status_code == 200 and silent.json()["results"]["bindings"] == [{}]


async def test_a_failing_peer_gives_a_502_problem(api, peer):
    url = f"{peer.base}/knowledge/kb/query"
    await remote(api, "no-creds", url)  # no Secret: the peer answers 401
    r = await query(api, JOIN.format(url=url))
    assert r.status_code == 502 and r.json()["code"] == "FEDERATION_ERROR", r.text


async def test_the_relay_listens_on_loopback_only(api):
    relay = api.app.state.relay
    assert relay.server.config.host == "127.0.0.1"
    async with httpx.AsyncClient() as client:
        r = await client.post(f"http://127.0.0.1:{relay.port}/federation/unknown", content=b"ASK {}")
    assert r.status_code == 403
