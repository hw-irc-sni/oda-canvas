"""The KM-FX step handlers. A handler gets the claimed task (with ``input``, the previous step's result) and the
internal client, and returns the task's result; it raises StepError to fail the task (retryable or not). Steps
without a handler yet complete as no-ops."""
from collections.abc import Awaitable, Callable

STEPS = ("KM-FX-01", "KM-FX-02", "KM-FX-03", "KM-FX-04", "KM-FX-05", "KM-FX-06")
Handler = Callable[[dict, object], Awaitable[dict]]


class StepError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


async def noop(task: dict, client) -> dict:
    return {"noop": True, **({"passed": task["input"]} if task.get("input") else {})}


def default_handlers(settings=None) -> dict[str, Handler]:
    from .km_fx_01 import handler as km_fx_01
    handlers: dict[str, Handler] = {step: noop for step in STEPS}
    handlers["KM-FX-01"] = km_fx_01(settings)
    return handlers
