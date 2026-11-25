"""Event-driven ingestion: provider callbacks, per-KB filters, coalescing, and hub registration on providers."""
import json

import httpx
import pytest
from sqlalchemy import select

from app.db.models import EventSource, Process, Task
from app.event_sources import Registrar, token_hash

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR = "knowledge-editor"
ALARM = {"id": "a-1", "@type": "Alarm", "alarmType": "equipmentAlarm", "perceivedSeverity": "critical",
         "state": "raised"}


def event(event_type="AlarmCreateEvent", resource=ALARM):
    return {"eventId": "e-1", "eventType": event_type, "event": {"alarm": resource}}


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), event_batch_max=3) as client:
        for kb, sources in [("alarms", [{"kind": "tmf642", "eventTypes": ["AlarmCreateEvent"]}]),
                            ("critical", [{"kind": "tmf642", "query": "perceivedSeverity=critical"}]),
                            ("docs", [{"kind": "upload"}])]:
            r = await client.post(f"{client.base}/knowledge", json={"id": kb, "name": kb, "storeKind": "graph",
                                                                    "source": sources}, headers=auth(EDITOR))
            assert r.status_code == 201, r.text
        async with client.app.state.sessions() as s:
            s.add(EventSource(id="es-1", kind="tmf642", dependency="tmf642", provider_url="http://alarms",
                              token_hash=token_hash("secret-token")))
            await s.commit()
        yield client


async def post(api, body, token="secret-token", kind="tmf642", suffix=""):
    return await api.post(f"{api.base}/eventSource/{kind}/{token}{suffix}", json=body)


async def processes_of(api):
    async with api.app.state.sessions() as s:
        procs = (await s.scalars(select(Process).order_by(Process.knowledge_id))).all()
        tasks = (await s.scalars(select(Task).where(Task.seq == 1))).all()
    return procs, {t.process_id: t for t in tasks}


async def test_an_alarm_event_creates_a_process_for_the_matching_kbs(api):
    r = await post(api, event())
    assert r.status_code == 204
    assert await api.app.state.batcher.flush_due(force=True)
    procs, first = await processes_of(api)
    assert [(p.knowledge_id, p.source) for p in procs] == [("alarms", "tmf642"), ("critical", "tmf642")]
    payload = first[procs[0].id].payload
    assert payload["source"] == "tmf642" and payload["params"]["resources"] == [ALARM]
    assert payload["params"]["eventTypes"] == ["AlarmCreateEvent"]


async def test_unknown_events_are_ignored(api):
    other = await post(api, event("AlarmAttributeValueChangeEvent", {**ALARM, "perceivedSeverity": "minor"}))
    assert other.status_code == 204  # neither KB wants it: "alarms" filters the type, "critical" the severity
    assert (await post(api, {"eventType": "Nothing"})).status_code == 204  # no resource
    assert await api.app.state.batcher.flush_due(force=True) == []
    assert (await processes_of(api))[0] == []
    # the v5 listener form reaches the same place
    assert (await post(api, event(), suffix="/listener/alarmCreateEvent")).status_code == 204
    assert len(await api.app.state.batcher.flush_due(force=True)) == 2


async def test_a_bad_token_gets_404(api):
    assert (await post(api, event(), token="guess")).status_code == 404
    assert (await post(api, event(), kind="tmf639")).status_code == 404


async def test_events_are_coalesced(api):
    for i in range(2):
        await post(api, event(resource={**ALARM, "id": f"a-{i}"}))
    assert await api.app.state.batcher.flush_due() == []  # the 5 s window is still open
    await post(api, event(resource={**ALARM, "id": "a-2"}))  # EVENT_BATCH_MAX (3) reached: flushed at once
    procs, first = await processes_of(api)
    resources = {p.knowledge_id: first[p.id].payload["params"]["resources"] for p in procs}
    assert [r["id"] for r in resources["alarms"]] == ["a-0", "a-1", "a-2"]


async def test_registration_on_providers(postgres_url, tmp_path):
    calls = []
    canvas_info = [{"id": "s1", "serviceCharacteristic": [
        {"name": "componentName", "value": "km-knowledgemanagement"}, {"name": "dependencyName", "value": "alarms"},
        {"name": "url", "value": "http://alarm-provider/tmf-api/alarmManagement/v5"}]}]

    def fake(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), json.loads(request.content) if request.content else None))
        if request.url.host == "info":
            return httpx.Response(200, json=canvas_info)
        if request.method == "POST":
            return httpx.Response(201, json={"id": f"hub-{len(calls)}", **json.loads(request.content)})
        return httpx.Response(204)

    async with running_api(postgres_url, str(tmp_path / "store"), canvas_info_url="http://info",
                           event_callback_base="https://km.example/km/tmf-api/knowledgeManagement/v5",
                           event_source_urls=json.dumps({"tmf639": "http://inventory/tmf-api/resourceInventory/v5"})) as api:
        registrar = Registrar(api.app.state.settings, api.app.state.sessions, transport=httpx.MockTransport(fake))
        await registrar.reconcile()
        assert calls == []  # no KB uses a TMF source yet
        await api.post(f"{api.base}/knowledge", json={"id": "a", "name": "a", "storeKind": "graph", "source": [
            {"kind": "tmf642", "dependentApiName": "alarms", "eventTypes": ["AlarmCreateEvent"]}]}, headers=auth(EDITOR))
        await api.post(f"{api.base}/knowledge", json={"id": "b", "name": "b", "storeKind": "graph", "source": [
            {"kind": "tmf642", "dependentApiName": "alarms", "eventTypes": ["AlarmClearedEvent"]},
            {"kind": "tmf639"}]}, headers=auth(EDITOR))
        await registrar.reconcile()
        hubs = {url: body for method, url, body in calls if method == "POST"}
        alarm_hub = hubs["http://alarm-provider/tmf-api/alarmManagement/v5/hub"]
        assert alarm_hub["query"] == "eventType=AlarmClearedEvent,AlarmCreateEvent"  # the union of the KBs' types
        assert alarm_hub["callback"].startswith("https://km.example/km/tmf-api/knowledgeManagement/v5/eventSource/tmf642/")
        assert "query" not in hubs["http://inventory/tmf-api/resourceInventory/v5/hub"]  # every TMF639 event
        token = alarm_hub["callback"].rsplit("/", 1)[1]
        async with api.app.state.sessions() as s:
            stored = (await s.scalars(select(EventSource).where(EventSource.kind == "tmf642"))).one()
        assert stored.token_hash == token_hash(token) and token not in stored.token_hash  # only the hash is kept
        calls.clear()
        await registrar.reconcile()
        assert {c[0] for c in calls} == {"GET"}  # unchanged: canvas-info is read, nothing is re-registered
        # the provider moves: the old hub is deleted and a new one registered
        canvas_info[0]["serviceCharacteristic"][2]["value"] = "http://alarm-provider-2/tmf-api/alarmManagement/v5"
        calls.clear()
        await registrar.reconcile()
        changes = [(m, u) for m, u, _ in calls if m != "GET"]
        assert changes == [("DELETE", f"http://alarm-provider/tmf-api/alarmManagement/v5/hub/{stored.hub_id}"),
                           ("POST", "http://alarm-provider-2/tmf-api/alarmManagement/v5/hub")]
        # no KB needs TMF639 any more: its hub is deleted
        async with api.app.state.sessions() as s:
            inventory = (await s.scalars(select(EventSource).where(EventSource.kind == "tmf639"))).one()
        await api.patch(f"{api.base}/knowledge/b", json={"source": [  # the same TMF642 interest, no TMF639
            {"kind": "tmf642", "dependentApiName": "alarms", "eventTypes": ["AlarmClearedEvent"]}]}, headers=auth(EDITOR))
        calls.clear()
        await registrar.reconcile()
        assert [(m, u) for m, u, _ in calls if m != "GET"] == [
            ("DELETE", f"http://inventory/tmf-api/resourceInventory/v5/hub/{inventory.hub_id}")]
        async with api.app.state.sessions() as s:
            kinds = sorted(s.kind for s in (await s.scalars(select(EventSource))).all())
        assert kinds == ["tmf642"]
        await registrar.aclose()


async def test_no_callback_base_no_registration(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store")) as api:
        def refuse(request):
            raise AssertionError("no call expected")
        registrar = Registrar(api.app.state.settings, api.app.state.sessions, transport=httpx.MockTransport(refuse))
        await registrar.reconcile()
        await registrar.aclose()
