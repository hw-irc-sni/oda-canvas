"""The TMF701 v5 processflow API (read-only): ingestion processes and their tasks."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, require_operation
from ..db.models import Process, Task
from ..errors import ApiError
from ..listing import list_response, present
from ..processes import present_process, present_task
from .deps import session

router = APIRouter()


@router.get("/process")
async def list_process(request: Request, s: AsyncSession = Depends(session),
                       _: Principal = Depends(require_operation("listProcess"))):
    settings = request.app.state.settings
    processes = (await s.scalars(select(Process).order_by(Process.created.desc(), Process.id))).all()
    tasks = (await s.scalars(select(Task))).all()
    by_process: dict[str, list] = {}
    for t in tasks:
        by_process.setdefault(t.process_id, []).append(t)
    return list_response(request, [present_process(settings, p, by_process.get(p.id, [])) for p in processes])


@router.get("/process/{id}")
async def retrieve_process(id: str, request: Request, s: AsyncSession = Depends(session),
                           _: Principal = Depends(require_operation("retrieveProcess"))):
    process = await s.get(Process, id)
    if process is None:
        raise ApiError(404, "404", "Not Found", f"No process '{id}'")
    tasks = (await s.scalars(select(Task).where(Task.process_id == id))).all()
    return present(request, present_process(request.app.state.settings, process, tasks))


@router.get("/task")
async def list_task(request: Request, s: AsyncSession = Depends(session),
                    _: Principal = Depends(require_operation("listTask"))):
    settings = request.app.state.settings
    tasks = (await s.scalars(select(Task).order_by(Task.created, Task.process_id, Task.chain, Task.seq))).all()
    return list_response(request, [present_task(settings, t) for t in tasks])


@router.get("/task/{id}")
async def retrieve_task(id: str, request: Request, s: AsyncSession = Depends(session),
                        _: Principal = Depends(require_operation("retrieveTask"))):
    task = await s.get(Task, id)
    if task is None:
        raise ApiError(404, "404", "Not Found", f"No task '{id}'")
    return present(request, present_task(request.app.state.settings, task))
