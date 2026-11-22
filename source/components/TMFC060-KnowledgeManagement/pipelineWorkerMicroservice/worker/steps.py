"""The KM-FX step handlers. A handler gets the task and the internal client and returns the task's result; it raises
StepError to fail the task (retryable or not). Steps without a handler yet complete as no-ops."""
from collections.abc import Awaitable, Callable

STEPS = ("KM-FX-01", "KM-FX-02", "KM-FX-03", "KM-FX-04", "KM-FX-05", "KM-FX-06")
Handler = Callable[[dict, object], Awaitable[dict]]


class StepError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


async def noop(task: dict, client) -> dict:
    return {"noop": True}


def default_handlers() -> dict[str, Handler]:
    return {step: noop for step in STEPS}
