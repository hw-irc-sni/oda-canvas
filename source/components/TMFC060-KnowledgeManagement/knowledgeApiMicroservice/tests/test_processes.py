"""Ingestion processes (TMF701 v5): ingest, task chains, SKIP LOCKED claims, leases, retries, failure, the read API."""
import asyncio

import pytest
from sqlalchemy import text

from app import processes
from app.db.models import Document, Process, Task

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR = "knowledge-editor"


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store")) as client:
        r = await client.post(f"{client.base}/knowledge", json={
            "id": "kb", "name": "KB", "storeKind": "graph", "source": [{"kind": "upload"}, {"kind": "tmf921"}]},
            headers=auth(EDITOR))
        assert r.status_code == 201, r.text
        yield client


async def upload(api, n, kb="kb"):
    ids = []
    for i in range(n):
        r = await api.post(f"{api.base}/knowledge/{kb}/document", files={"file": (f"d{i}.md", f"# doc {i}".encode(),
                                                                                  "text/markdown")},
                           headers=auth(EDITOR))
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


async def ingest(api, kb="kb", role=EDITOR, **body):
    return await api.post(f"{api.base}/knowledge/{kb}/ingest", json=body, headers=auth(role))


async def claim(api, worker="w1"):
    async with api.app.state.sessions() as s:
        return await processes.claim(s, api.app.state.settings, worker)


async def complete(api, task, worker="w1"):
    async with api.app.state.sessions() as s:
        await processes.complete(s, task.id, worker, {"ok": True})


async def fail(api, task, worker="w1", retryable=True, error="boom"):
    async with api.app.state.sessions() as s:
        await processes.fail(s, api.app.state.settings, task.id, worker, error, retryable)


async def sql(api, statement, **params):
    async with api.app.state.sessions() as s:
        await s.execute(text(statement), params)
        await s.commit()


async def get(api, model, key):
    async with api.app.state.sessions() as s:
        return await s.get(model, key)


async def run_chain(api, worker="w1"):
    """Claim and complete tasks until none is left."""
    while (task := await claim(api, worker)) is not None:
        await complete(api, task, worker)


async def test_ingest_creates_a_process_with_a_chain_per_document(api):
    docs = await upload(api, 2)
    r = await ingest(api, source="upload")
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["@type"] == "ProcessRef" and ref["id"].startswith("pf-")
    assert ref["href"].endswith(f"/km-knowledgemanagement/tmf-api/process/v5/process/{ref['id']}")
    process = (await api.get(ref["href"], headers=auth())).json()
    assert process["state"] == "acknowledged" and len(process["task"]) == 12
    tasks = (await api.get(f"{api.base.split('/tmf-api')[0]}/tmf-api/process/v5/task", params={"processId": ref["id"]},
                           headers=auth())).json()
    assert sorted({t["documentId"] for t in tasks}) == sorted(docs)
    assert [t["step"] for t in tasks if t["documentId"] == docs[0]] == list(processes.STEPS)
    assert (await get(api, Document, docs[0])).last_process_id == ref["id"]


async def test_concurrent_claimers_never_get_the_same_task(api):
    await upload(api, 10)
    await ingest(api)
    claimed = await asyncio.gather(*(claim(api, f"w{i}") for i in range(10)))
    ids = [t.id for t in claimed if t is not None]
    assert len(ids) == 10 and len(set(ids)) == 10  # ten first steps, one each
    assert {t.step for t in claimed} == {"KM-FX-01"}
    assert await claim(api, "late") is None  # every next step waits for its previous one


async def test_steps_run_in_order_within_a_chain_and_chains_in_parallel(api):
    a, b = await upload(api, 2)
    await ingest(api)
    first = await claim(api)
    second = await claim(api)
    assert {first.payload["documentId"], second.payload["documentId"]} == {a, b}
    assert await claim(api) is None
    await complete(api, first)
    nxt = await claim(api)
    assert nxt.step == "KM-FX-02" and nxt.chain == first.chain
    process = await get(api, Process, first.process_id)
    assert process.state == "inProgress" and process.started is not None


async def test_an_expired_lease_is_reclaimed(api):
    await upload(api, 1)
    await ingest(api)
    task = await claim(api, "w1")
    await sql(api, "UPDATE task SET locked_at = now() - interval '400 seconds' WHERE id = :id", id=task.id)
    again = await claim(api, "w2")
    assert again.id == task.id and again.attempts == 2 and again.locked_by == "w2"
    with pytest.raises(processes.LeaseLost):
        await complete(api, task, "w1")  # the first worker lost it
    async with api.app.state.sessions() as s:
        await processes.heartbeat(s, again.id, "w2")
    await complete(api, again, "w2")


async def test_retry_delay_and_attempt_limit(api):
    a, b = await upload(api, 2)
    ref = (await ingest(api)).json()
    task = await claim(api)
    other = await claim(api)
    await fail(api, task)  # attempt 1 failed: rescheduled 30 s later
    assert (await get(api, Task, task.id)).state == "acknowledged"
    assert await claim(api) is None  # not due yet
    for attempt in (2, 3):
        await sql(api, "UPDATE task SET available_at = now() WHERE id = :id", id=task.id)
        again = await claim(api)
        assert again.id == task.id and again.attempts == attempt
        await fail(api, again, error=f"boom {attempt}")
    failed = await get(api, Task, task.id)
    assert failed.state == "failed" and failed.error == "boom 3"
    doc = await get(api, Document, task.payload["documentId"])
    assert doc.ingestion_state == "failed"
    process = (await api.get(ref["href"], headers=auth())).json()
    assert process["state"] == "inProgress"  # the other chain is still running
    await complete(api, other)
    await run_chain(api)
    process = (await api.get(ref["href"], headers=auth())).json()
    assert process["state"] == "failed" and "boom 3" in process["errorMessage"] and "completionDate" in process
    assert (await get(api, Document, other.payload["documentId"])).ingestion_state == "ingested"
    skipped = (await api.get(f"{api.base.split('/tmf-api')[0]}/tmf-api/process/v5/task",
                             params={"processId": ref["id"], "documentId": task.payload["documentId"]},
                             headers=auth())).json()
    assert [t["state"] for t in skipped] == ["failed"] * 6
    assert skipped[1]["errorMessage"] == "skipped: KM-FX-01 failed"


async def test_a_non_retryable_failure_fails_at_once(api):
    await upload(api, 1)
    ref = (await ingest(api)).json()
    await fail(api, await claim(api), retryable=False, error="cannot parse")
    assert (await get(api, Process, ref["id"])).state == "failed"


async def test_completion(api):
    docs = await upload(api, 2)
    ref = (await ingest(api)).json()
    await run_chain(api)
    process = (await api.get(ref["href"], headers=auth())).json()
    assert process["state"] == "completed" and "errorMessage" not in process
    for d in docs:
        assert (await get(api, Document, d)).ingestion_state == "ingested"
    # everything is ingested now
    assert (await ingest(api)).status_code == 409
    again = await ingest(api, documentId=[docs[0]])  # a named document can be re-ingested
    assert again.status_code == 201


async def test_ingest_conflicts_and_errors(api):
    docs = await upload(api, 1)
    assert (await ingest(api, source="tmf639")).status_code == 409  # not a source of the KB
    assert (await ingest(api, source="tmf921", params={"filter": "x"})).status_code == 201  # one batch chain
    assert (await ingest(api, documentId=docs)).status_code == 201
    assert (await ingest(api, documentId=docs)).status_code == 409  # already in progress
    assert (await ingest(api)).status_code == 409  # nothing left to ingest
    assert (await ingest(api, documentId=["doc-nope"])).status_code == 400
    assert (await ingest(api, source="ftp")).status_code == 400
    assert (await ingest(api, documentId="x")).status_code == 400
    assert (await ingest(api, role="knowledge-viewer")).status_code == 403
    assert (await ingest(api, kb="nope")).status_code == 404
    await sql(api, "UPDATE knowledge SET lifecycle_status = 'deprecated' WHERE id = 'kb'")
    assert (await ingest(api, documentId=docs)).status_code == 409


async def test_process_read_api(api):
    await upload(api, 1)
    ref = (await ingest(api)).json()
    base = f"{api.base.split('/tmf-api')[0]}/tmf-api/process/v5"
    listed = await api.get(f"{base}/process", headers=auth("knowledge-viewer"))
    assert [p["id"] for p in listed.json()] == [ref["id"]] and listed.headers["x-total-count"] == "1"
    process = (await api.get(f"{base}/process/{ref['id']}", headers=auth())).json()
    assert process["knowledge"]["id"] == "kb" and process["description"].startswith("Ingestion from upload")
    task_id = process["task"][0]["id"]
    task = (await api.get(f"{base}/task/{task_id}", headers=auth())).json()
    assert task["process"]["id"] == ref["id"] and task["attempt"] == 0 and task["state"] == "acknowledged"
    assert (await api.get(f"{base}/process/pf-nope", headers=auth())).status_code == 404
    assert (await api.get(f"{base}/task/tk-nope", headers=auth())).status_code == 404
    assert (await api.get(f"{base}/process")).status_code == 401


async def test_chains_finishing_together_close_the_process(api):
    """Regression: two chains completing at the same moment must still close their process (each transaction used
    to see the other's task as open)."""
    for n in range(8):
        a, b = [(await api.post(f"{api.base}/knowledge/kb/document", headers=auth(EDITOR), files={
            "file": (f"r{n}{x}.md", f"# round {n} {x}".encode(), "text/markdown")})).json()["id"] for x in "ab"]
        ref = (await ingest(api, documentId=[a, b])).json()
        await sql(api, "UPDATE task SET state = 'completed' WHERE process_id = :p AND seq < 6", p=ref["id"])
        first, second = await claim(api, "w1"), await claim(api, "w2")
        assert {first.seq, second.seq} == {6}
        await asyncio.gather(complete(api, first, "w1"), complete(api, second, "w2"))
        assert (await get(api, Process, ref["id"])).state == "completed"
