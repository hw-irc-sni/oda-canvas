"""The worker loop against the real API's internal port: no-op chains, two instances, retries, shutdown, health."""
import asyncio
import os
import time
from collections import Counter

import httpx
import pytest

from worker.client import InternalClient
from worker.config import Settings
from worker.health import health_app
from worker.loop import Worker
from worker.steps import STEPS, StepError, default_handlers

pytestmark = pytest.mark.integration


async def ingest(api, n):
    for i in range(n):
        r = await api.post(f"{api.base}/knowledge/kb/document", files={"file": (f"d{i}.md", f"# {i}".encode(),
                                                                                 "text/markdown")})
        assert r.status_code == 201, r.text
    r = await api.post(f"{api.base}/knowledge/kb/ingest", json={})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def process(api, pid):
    return (await api.get(f"/km/tmf-api/process/v5/process/{pid}")).json()


async def wait_done(api, pid, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        p = await process(api, pid)
        if p["state"] in ("completed", "failed"):
            return p
        await asyncio.sleep(0.1)
    raise AssertionError(f"process {pid} still {p['state']}")


def make_worker(api, name, handlers=None, **overrides):
    settings = Settings(internal_api_url=api.internal_url, internal_token_file=api.token_file, worker_id=name,
                        worker_poll_interval=0.05, **overrides)
    client = InternalClient(settings.internal_api_url, settings.internal_token_file, name)
    return Worker(settings, client, handlers)


async def test_a_noop_chain_completes_the_process(api):
    pid = await ingest(api, 1)
    worker = make_worker(api, "w1")
    worker.start()
    try:
        p = await wait_done(api, pid)
    finally:
        await worker.stop()
    assert p["state"] == "completed" and len(worker.processed) == 6
    doc = (await api.get(f"{api.base}/knowledge/kb/document")).json()[0]
    assert doc["ingestionState"] == "ingested"


async def test_two_workers_process_each_task_exactly_once(api):
    pid = await ingest(api, 5)
    seen = Counter()

    def recording(step):
        async def handler(task, client):
            seen[task["id"]] += 1
            await asyncio.sleep(0.01)
            return {"step": step}
        return handler
    handlers = {s: recording(s) for s in STEPS}
    workers = [make_worker(api, f"w{i}", handlers, worker_concurrency=2) for i in range(2)]
    for w in workers:
        w.start()
    try:
        p = await wait_done(api, pid, timeout=120)  # 30 tasks of HTTP round trips: slow CI runners need room
    finally:
        for w in workers:
            await w.stop()
    assert p["state"] == "completed"
    assert len(seen) == 30 and set(seen.values()) == {1}  # 5 documents × 6 steps, each once
    assert all(w.processed for w in workers)  # both took part
    assert sorted(workers[0].processed + workers[1].processed) == sorted(seen)


async def test_retryable_and_final_errors(api):
    pid = await ingest(api, 1)
    attempts = Counter()

    async def flaky(task, client):
        attempts[task["id"]] += 1
        if attempts[task["id"]] == 1:
            raise StepError("transient")
        return {}

    async def broken(task, client):
        raise StepError("cannot parse", retryable=False)
    api.app.state.settings.task_retry_delay = 0
    worker = make_worker(api, "w1", {**default_handlers(), "KM-FX-01": flaky, "KM-FX-02": broken})
    worker.start()
    try:
        p = await wait_done(api, pid)
    finally:
        await worker.stop()
    assert list(attempts.values()) == [2]  # retried once, then done
    assert p["state"] == "failed" and "cannot parse" in p["errorMessage"]


async def test_graceful_shutdown(api):
    pid = await ingest(api, 2)
    started = asyncio.Event()

    async def slow(task, client):
        started.set()
        await asyncio.sleep(0.3)
        return {}

    async def stuck(task, client):
        started.set()
        await asyncio.sleep(60)
        return {}
    finishing = make_worker(api, "w1", {**default_handlers(), "KM-FX-01": slow}, worker_concurrency=1,
                            worker_shutdown_grace=5)
    finishing.start()
    await started.wait()
    await finishing.stop()
    assert len(finishing.processed) == 1  # the in-flight task finished within the grace period
    started.clear()
    hanging = make_worker(api, "w2", {**default_handlers(), "KM-FX-01": stuck}, worker_concurrency=1,
                          worker_shutdown_grace=0.2)
    hanging.start()
    await started.wait()
    await hanging.stop()
    tasks = (await api.get("/km/tmf-api/process/v5/task", params={"processId": pid, "step": "KM-FX-01"})).json()
    released = [t for t in tasks if t["state"] == "acknowledged" and t.get("errorMessage") == "worker shut down"]
    assert len(released) == 1  # failed as retryable: claimable again


async def test_a_lost_lease_is_abandoned(api):
    await ingest(api, 1)
    from sqlalchemy import text

    async def lose(task, client):
        async with api.app.state.sessions() as s:  # another worker took it over
            await s.execute(text("UPDATE task SET locked_by = 'other' WHERE id = :id"), {"id": task["id"]})
            await s.commit()
        return {}
    worker = make_worker(api, "w1", {**default_handlers(), "KM-FX-01": lose}, worker_concurrency=1)
    worker.start()
    await asyncio.sleep(0.5)
    await worker.stop()
    assert worker.processed == []


def touch(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.utime(path, ns=(time.time_ns() + 10**9,) * 2)


async def test_health_and_ready_and_token_rotation(api, tmp_path):
    own_token = tmp_path / "worker-token"
    touch(own_token, open(api.token_file).read())
    worker = make_worker(api, "w1")
    worker.client = InternalClient(api.internal_url, str(own_token), "w1")
    app = health_app(worker)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w") as client:
        try:
            assert (await client.get("/health")).status_code == 503  # not started
            worker.start()
            health = await client.get("/health")
            assert health.status_code == 200 and health.json()["worker"] == "w1"
            assert (await client.get("/ready")).status_code == 200
            touch(own_token, "wrong")  # the worker re-reads its token file
            assert (await client.get("/ready")).status_code == 503
            touch(own_token, open(api.token_file).read())
            assert (await client.get("/ready")).status_code == 200
        finally:
            await worker.stop()


async def test_km_fx_01_result_reaches_km_fx_02(api):
    content = b"# Runbook\n\nMains power alarms come first.\n\n## Escalation\n\nCall the NOC."
    r = await api.post(f"{api.base}/knowledge/kb/document", files={"file": ("r.md", content, "text/markdown")})
    doc_id = r.json()["id"]
    pid = (await api.post(f"{api.base}/knowledge/kb/ingest", json={})).json()["id"]
    seen = {}

    async def capture(task, client):
        seen["input"] = task["input"]
        return {}
    from worker.steps import noop
    worker = make_worker(api, "w1", {**default_handlers(), "KM-FX-02": capture,
                                         **{s: noop for s in ("KM-FX-03", "KM-FX-04", "KM-FX-05", "KM-FX-06")}})
    worker.start()
    try:
        assert (await wait_done(api, pid))["state"] == "completed"
    finally:
        await worker.stop()
    result = seen["input"]
    assert result["kind"] == "text" and result["documentId"] == doc_id
    assert [c["section"] for c in result["chunks"]] == ["Runbook", "Escalation"]
    assert all(result["text"][c["start"]:c["end"]] == c["text"] for c in result["chunks"])


async def test_a_result_over_the_cap_fails_the_step(api):
    pid = await ingest(api, 1)
    api.app.state.settings.task_result_max_bytes = 100
    worker = make_worker(api, "w1")
    worker.start()
    try:
        p = await wait_done(api, pid)
    finally:
        await worker.stop()
    assert p["state"] == "failed" and "KM-FX-01 result too large" in p["errorMessage"]


async def test_a_reporting_error_does_not_stop_the_slot(api):
    pid = await ingest(api, 1)
    worker = make_worker(api, "w1", worker_concurrency=1)
    real_complete, calls = worker.client.complete, []

    async def flaky_complete(task_id, result):
        calls.append(task_id)
        if len(calls) == 1:
            raise RuntimeError("the API is restarting")
        return await real_complete(task_id, result)
    worker.client.complete = flaky_complete
    from sqlalchemy import text
    worker.start()
    try:
        # the first report fails; the slot keeps claiming. Expire the stuck lease so the task is taken again.
        for _ in range(100):
            if len(calls) >= 1:
                break
            await asyncio.sleep(0.05)
        async with api.app.state.sessions() as s:
            await s.execute(text("UPDATE task SET locked_at = now() - interval '1 hour' WHERE state = 'inProgress'"))
            await s.commit()
        p = await wait_done(api, pid, timeout=60)
    finally:
        await worker.stop()
    assert p["state"] == "completed" and len(calls) >= 7  # the failed report, its retry, and the other 5 steps
    assert all(not s.done() for s in worker.slots) or worker.stopping.is_set()
