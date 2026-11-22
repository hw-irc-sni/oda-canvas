"""/hub subscriptions and event delivery through the outbox to a real local listener."""
import asyncio
import socket

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from sqlalchemy import select, text

from app.db.models import EventDelivery
from app.events import CallbackRefused, check_callback

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR, ONT_EDITOR, REVIEWER, APPROVER = "knowledge-editor", "ontology-editor", "ontology-reviewer", "ontology-approver"


class Listener:
    def __init__(self):
        self.received: list[tuple[str, dict]] = []
        self.statuses: list[int] = []  # answers to give, then 204
        self.app = FastAPI()
        self.app.add_api_route("/cb/listener/{name}", self.receive, methods=["POST"])

    async def receive(self, name: str, request: Request):
        status = self.statuses.pop(0) if self.statuses else 204
        if status < 300:
            self.received.append((name, await request.json()))
        return Response(status_code=status)


@pytest.fixture
async def listener():
    fake = Listener()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(fake.app, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.02)
    fake.callback = f"http://127.0.0.1:{sock.getsockname()[1]}/cb"
    yield fake
    server.should_exit = True
    await task


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), governance_require_validation=False,
                           event_callback_allowed_hosts=["127.0.0.1"], event_poll_interval=3600,
                           event_retry_delays=[0, 0]) as client:
        yield client


async def hub(api, callback, role="knowledge-viewer", **extra):
    return await api.post(f"{api.base}/hub", json={"callback": callback, **extra}, headers=auth(role))


async def drain(api):
    while await api.app.state.dispatcher.run_once():
        pass


async def deliveries(api):
    async with api.app.state.sessions() as s:
        return (await s.scalars(select(EventDelivery).order_by(EventDelivery.created))).all()


async def every_event(api):
    spec = f"{api.base}/knowledgeSpecification"
    await api.post(spec, json={"id": "m", "name": "m", "iri": "https://example.org/m", "specKind": "ontology",
                               "source": {"kind": "upload"}}, headers=auth(ONT_EDITOR))
    await api.patch(f"{spec}/m", json={"description": "d"}, headers=auth(ONT_EDITOR))
    await api.patch(f"{spec}/m", json={"lifecycleStatus": "proposed"}, headers=auth(ONT_EDITOR))
    await api.delete(f"{spec}/m", headers=auth(APPROVER))
    kb = f"{api.base}/knowledge"
    await api.post(kb, json={"id": "k", "name": "k", "storeKind": "graph"}, headers=auth(EDITOR))
    await api.patch(f"{kb}/k", json={"description": "d"}, headers=auth(EDITOR))
    await api.patch(f"{kb}/k", json={"lifecycleStatus": "proposed"}, headers=auth(EDITOR))
    await api.delete(f"{kb}/k", headers=auth(EDITOR))


async def test_each_event_type_is_received_once(api, listener):
    r = await hub(api, listener.callback)
    assert r.status_code == 201 and r.json()["callback"] == listener.callback and r.json()["id"].startswith("hub-")
    await every_event(api)
    await drain(api)
    names = [name for name, _ in listener.received]
    assert sorted(names) == sorted(f"{r}{k}Event" for r in ("knowledgeSpecification", "knowledge")
                                   for k in ("Create", "AttributeValueChange", "StateChange", "Delete"))
    created = dict(listener.received)["knowledgeCreateEvent"]
    assert created["eventType"] == "KnowledgeCreateEvent" and created["@type"] == "KnowledgeCreateEvent"
    assert created["event"]["knowledge"]["id"] == "k" and created["eventId"] and created["eventTime"]
    assert dict(listener.received)["knowledgeSpecificationStateChangeEvent"]["event"]["knowledgeSpecification"][
        "lifecycleStatus"] == "proposed"
    assert {d.state for d in await deliveries(api)} == {"delivered"}


async def test_event_type_filter_and_delete_stops_delivery(api, listener):
    r = await hub(api, listener.callback, query="eventType=KnowledgeCreateEvent,KnowledgeDeleteEvent")
    hub_id = r.json()["id"]
    assert r.json()["query"] == "eventType=KnowledgeCreateEvent,KnowledgeDeleteEvent"
    await every_event(api)
    await drain(api)
    assert sorted(n for n, _ in listener.received) == ["knowledgeCreateEvent", "knowledgeDeleteEvent"]
    assert (await api.delete(f"{api.base}/hub/{hub_id}", headers=auth())).status_code == 204
    assert (await api.get(f"{api.base}/hub/{hub_id}", headers=auth())).status_code == 404
    await api.post(f"{api.base}/knowledge", json={"id": "k2", "name": "k2", "storeKind": "graph"}, headers=auth(EDITOR))
    await drain(api)
    assert len(listener.received) == 2


async def test_retries_then_failed(api, listener):
    await hub(api, listener.callback, query="eventType=KnowledgeCreateEvent")
    listener.statuses = [500]
    await api.post(f"{api.base}/knowledge", json={"id": "a", "name": "a", "storeKind": "graph"}, headers=auth(EDITOR))
    await drain(api)  # 500, then retried at once (delay 0) and delivered
    assert [n for n, _ in listener.received] == ["knowledgeCreateEvent"]
    row = (await deliveries(api))[0]
    assert row.state == "delivered" and row.attempts == 2
    listener.statuses = [500, 503, 502]
    await api.post(f"{api.base}/knowledge", json={"id": "b", "name": "b", "storeKind": "graph"}, headers=auth(EDITOR))
    await drain(api)  # three tries (two delays), then failed
    row = (await deliveries(api))[1]
    assert row.state == "failed" and row.attempts == 3 and row.last_error == "HTTP 502"
    assert len(listener.received) == 1


async def test_backoff_waits(postgres_url, tmp_path, listener):
    async with running_api(postgres_url, str(tmp_path / "store"), event_callback_allowed_hosts=["127.0.0.1"],
                           event_poll_interval=3600) as api:
        await hub(api, listener.callback)
        listener.statuses = [500]
        await api.post(f"{api.base}/knowledge", json={"id": "a", "name": "a", "storeKind": "graph"},
                       headers=auth(EDITOR))
        await drain(api)
        assert listener.received == []  # the retry is 10 s away
        async with api.app.state.sessions() as s:
            await s.execute(text("UPDATE event_delivery SET next_attempt_at = now()"))
            await s.commit()
        await drain(api)
        assert [n for n, _ in listener.received] == ["knowledgeCreateEvent"]


async def test_callback_guard(api):
    for bad in ["ftp://example.org/x", "http://169.254.169.254/latest", "http://10.1.2.3/cb", "http://[::1]/cb",
                "not a url"]:
        r = await hub(api, bad)
        assert r.status_code == 400, bad
    with pytest.raises(CallbackRefused):
        await check_callback("http://127.0.0.1:9/cb", [])  # loopback refused without the allowlist
    await check_callback("http://127.0.0.1:9/cb", ["127.0.0.1"])
    await check_callback("http://listener.ns.svc.cluster.local/cb", ["*.svc.cluster.local"])  # not even resolved
    assert (await hub(api, "http://127.0.0.1:9/cb", query="state=x")).status_code == 400
    assert (await hub(api, "http://127.0.0.1:9/cb", query="eventType=")).status_code == 400


async def test_ownership_and_roles(api, listener):
    mine = (await hub(api, listener.callback)).json()["id"]
    other = auth("knowledge-viewer", extra={"sub": "user-2"})
    assert (await api.get(f"{api.base}/hub/{mine}", headers=other)).status_code == 404
    assert (await api.delete(f"{api.base}/hub/{mine}", headers=other)).status_code == 404
    assert (await api.get(f"{api.base}/hub/{mine}", headers=auth())).json()["callback"] == listener.callback
    assert (await api.post(f"{api.base}/hub", json={"callback": listener.callback})).status_code == 401
    assert (await api.post(f"{api.base}/hub", json={}, headers=auth())).status_code == 400
