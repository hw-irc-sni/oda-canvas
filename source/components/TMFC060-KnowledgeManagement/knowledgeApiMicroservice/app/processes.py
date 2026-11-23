"""Ingestion as TMF701 v5 processes: one process per ingest, one chain of six tasks (KM-FX-01 … KM-FX-06) per
document or source batch, claimed by pipeline workers from Postgres.

- A task is claimable when it is ``acknowledged``, due (``available_at``), and the previous step of its chain has
  completed, or when it is ``inProgress`` and its lease (``TASK_LEASE_SECONDS``, renewed by ``heartbeat``) has
  expired. ``claim`` takes one with ``SELECT … FOR UPDATE SKIP LOCKED``, so concurrent workers never get the same task.
- ``fail`` reschedules a task (``TASK_RETRY_DELAY`` × attempt) until ``TASK_MAX_ATTEMPTS``; then the task, the rest
  of its chain and its document are failed. Other chains go on; the process ends ``failed`` (with the first error)
  once nothing is open, else ``completed``.
- Times come from the database clock, so workers on different hosts agree.
"""
import secrets
from datetime import datetime

from sqlalchemy import exists, func, literal_column, select, text, update

from .config import Settings
from .db.models import Document, Process, Task
from .errors import ApiError

STEPS = ("KM-FX-01", "KM-FX-02", "KM-FX-03", "KM-FX-04", "KM-FX-05", "KM-FX-06")
OPEN = ("acknowledged", "inProgress")
SOURCES = ("upload", "tmf639", "tmf638", "tmf642", "tmf628", "tmf921", "remote")


class LeaseLost(Exception):
    """The task is no longer held by this worker (its lease expired and another worker took it)."""


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def bad_request(message: str) -> ApiError:
    return ApiError(400, "400", "Bad Request", message)


def conflict(message: str) -> ApiError:
    return ApiError(409, "409", "Conflict", message)


# ---- presentation (TMF701 v5 subset) ---------------------------------------------------------------------------
def process_ref(settings: Settings, process_id: str) -> dict:
    return {"@type": "ProcessRef", "@referredType": "Process", "id": process_id,
            "href": f"{settings.process_base_path}/process/{process_id}"}


def present_process(settings: Settings, p: Process, tasks: list[Task]) -> dict:
    out = {"@type": "Process", "id": p.id, "href": f"{settings.process_base_path}/process/{p.id}", "state": p.state,
           "description": f"Ingestion from {p.source} into the knowledge-base {p.knowledge_id}",
           "creationDate": iso(p.created), "completionDate": iso(p.completed), "errorMessage": p.error,
           "knowledge": {"id": p.knowledge_id, "href": f"{settings.base_path}/knowledge/{p.knowledge_id}"},
           "task": [{"@type": "TaskRef", "id": t.id, "href": f"{settings.process_base_path}/task/{t.id}"}
                    for t in sorted(tasks, key=lambda t: (t.chain, t.seq))]}
    return {k: v for k, v in out.items() if v is not None}


def present_task(settings: Settings, t: Task) -> dict:
    out = {"@type": "Task", "id": t.id, "href": f"{settings.process_base_path}/task/{t.id}", "processId": t.process_id,
           "process": process_ref(settings, t.process_id), "step": t.step, "documentId": t.payload.get("documentId"),
           "state": t.state, "attempt": t.attempts, "errorMessage": t.error, "creationDate": iso(t.created),
           "completionDate": iso(t.updated) if t.state in ("completed", "failed") else None}
    return {k: v for k, v in out.items() if v is not None}


# ---- starting an ingestion -------------------------------------------------------------------------------------
async def in_progress(session, document_ids: list[str]) -> set[str]:
    rows = await session.scalars(select(Task.chain).where(Task.chain.in_(document_ids), Task.state.in_(OPEN)))
    return set(rows.all())


async def start_ingestion(session, settings: Settings, kb, payload: dict) -> dict:
    """Create the process and its task chains for POST /knowledge/{id}/ingest; returns the ProcessRef."""
    if kb.lifecycle_status == "deprecated":
        raise conflict("A deprecated knowledge-base cannot ingest")
    source = payload.get("source", "upload")
    if source not in SOURCES:
        raise bad_request(f"source must be one of {', '.join(SOURCES)}")
    if source != "upload" and source not in {s.get("kind") for s in kb.body.get("source") or []}:
        raise conflict(f"The knowledge-base has no source of kind {source}")
    chains: list[tuple[str, dict]] = []
    if source == "upload":
        named = payload.get("documentId")
        if named is not None and (not isinstance(named, list) or not all(isinstance(d, str) for d in named)):
            raise bad_request("documentId must be a list of document ids")
        docs = (await session.scalars(select(Document).where(Document.knowledge_id == kb.id))).all()
        by_id = {d.id: d for d in docs}
        if named:
            unknown = [d for d in named if d not in by_id]
            if unknown:
                raise bad_request(f"No such documents in the knowledge-base: {', '.join(unknown)}")
            busy = await in_progress(session, named)
            if busy:
                raise conflict(f"Already being ingested: {', '.join(sorted(busy))}")
            chosen = [by_id[d] for d in dict.fromkeys(named)]
        else:
            busy = await in_progress(session, list(by_id))
            chosen = [d for d in docs if d.ingestion_state != "ingested" and d.id not in busy]
            if not chosen:
                raise conflict("Nothing to ingest: every document is ingested or being ingested")
        chains = [(d.id, {"kbId": kb.id, "documentId": d.id}) for d in chosen]
    else:
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise bad_request("params must be an object")
        chains = [(f"batch-{secrets.token_hex(4)}", {"kbId": kb.id, "source": source, "params": params})]
    process = Process(id=f"pf-{secrets.token_hex(6)}", knowledge_id=kb.id, state="acknowledged", source=source,
                      request=payload)
    session.add(process)
    await session.flush()
    for chain, task_payload in chains:
        for seq, step in enumerate(STEPS, start=1):
            session.add(Task(id=f"tk-{secrets.token_hex(6)}", process_id=process.id, chain=chain, step=step, seq=seq,
                             state="acknowledged", attempts=0, payload=task_payload))
        if "documentId" in task_payload:
            doc = await session.get(Document, chain)
            doc.last_process_id, doc.ingestion_state = process.id, "pending"
    await session.commit()
    return process_ref(settings, process.id)


# ---- the worker side -------------------------------------------------------------------------------------------
CLAIMABLE = text("""
SELECT t.id FROM task t
WHERE ((t.state = 'acknowledged' AND t.available_at <= now())
       OR (t.state = 'inProgress' AND t.locked_at < now() - make_interval(secs => :lease)))
  AND NOT EXISTS (SELECT 1 FROM task p WHERE p.process_id = t.process_id AND p.chain = t.chain
                  AND p.seq < t.seq AND p.state <> 'completed')
ORDER BY t.available_at, t.created, t.seq
LIMIT 1
FOR UPDATE OF t SKIP LOCKED
""")


async def claim(session, settings: Settings, worker: str) -> Task | None:
    """Take the next claimable task for this worker (committed), or None."""
    while True:
        task_id = await session.scalar(CLAIMABLE, {"lease": settings.task_lease_seconds})
        if task_id is None:
            await session.rollback()
            return None
        task = await session.get(Task, task_id)
        if task.state == "inProgress" and task.attempts >= settings.task_max_attempts:
            await _fail_for_good(session, task, f"lease expired after {task.attempts} attempts")
            await session.commit()
            continue
        task.state, task.locked_by, task.locked_at = "inProgress", worker, func.now()
        task.attempts += 1
        await session.execute(update(Process).where(Process.id == task.process_id, Process.state == "acknowledged")
                              .values(state="inProgress", started=func.now()))
        await session.commit()
        await session.refresh(task)
        return task


async def _held(session, task_id: str, worker: str) -> Task:
    task = await session.get(Task, task_id, with_for_update=True)
    if task is None or task.state != "inProgress" or task.locked_by != worker:
        await session.rollback()
        raise LeaseLost(task_id)
    return task


async def heartbeat(session, task_id: str, worker: str) -> None:
    task = await _held(session, task_id, worker)
    task.locked_at = func.now()
    await session.commit()


async def complete(session, task_id: str, worker: str, result: dict | None = None) -> None:
    task = await _held(session, task_id, worker)
    task.state, task.result, task.locked_by = "completed", result or {}, None
    if task.seq == len(STEPS) and task.payload.get("documentId"):
        doc = await session.get(Document, task.payload["documentId"])
        if doc is not None:
            doc.ingestion_state = "ingested"
    await session.flush()
    await _close_if_done(session, task.process_id)
    await session.commit()


async def fail(session, settings: Settings, task_id: str, worker: str, error: str, retryable: bool = True) -> None:
    task = await _held(session, task_id, worker)
    task.error = error
    if retryable and task.attempts < settings.task_max_attempts:
        task.state, task.locked_by, task.locked_at = "acknowledged", None, None
        delay = int(settings.task_retry_delay * task.attempts)
        task.available_at = func.now() + literal_column(f"interval '{delay} seconds'")
    else:
        await _fail_for_good(session, task, error)
    await session.commit()


async def _fail_for_good(session, task: Task, error: str) -> None:
    task.state, task.error, task.locked_by = "failed", error, None
    await session.execute(update(Task).where(Task.process_id == task.process_id, Task.chain == task.chain,
                                             Task.seq > task.seq, Task.state.in_(OPEN))
                          .values(state="failed", error=f"skipped: {task.step} failed"))
    if task.payload.get("documentId"):
        doc = await session.get(Document, task.payload["documentId"])
        if doc is not None:
            doc.ingestion_state = "failed"
    process = await session.get(Process, task.process_id)
    if process.error is None:
        process.error = f"{task.step} of {task.chain}: {error}"
    await session.flush()
    await _close_if_done(session, task.process_id)


async def _close_if_done(session, process_id: str) -> None:
    # Lock the process first: two chains finishing at once would otherwise each see the other's task as still open
    # (neither committed yet) and leave the process inProgress. Whoever locks second sees the first's commit.
    await session.execute(select(Process.id).where(Process.id == process_id).with_for_update())
    open_left = await session.scalar(select(exists().where(Task.process_id == process_id, Task.state.in_(OPEN))))
    if open_left:
        return
    failed = await session.scalar(select(exists().where(Task.process_id == process_id, Task.state == "failed")))
    await session.execute(update(Process).where(Process.id == process_id).values(
        state="failed" if failed else "completed", completed=func.now()))


__all__ = ["STEPS", "LeaseLost", "claim", "complete", "fail", "heartbeat", "start_ingestion"]
