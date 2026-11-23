"""The claim loop: N slots, each claims a task, keeps its lease alive with heartbeats, runs the step handler and
reports the outcome. On shutdown it stops claiming, lets in-flight tasks finish for the grace period, then fails the
rest as retryable so another replica takes them at once."""
import asyncio
import logging
import time

from .client import InternalClient, LeaseLost, ResultTooLarge
from .config import Settings
from .steps import Handler, StepError, default_handlers

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings, client: InternalClient, handlers: dict[str, Handler] | None = None):
        self.settings = settings
        self.client = client
        self.handlers = handlers or default_handlers(settings)
        self.stopping = asyncio.Event()
        self.in_flight: dict[str, dict] = {}
        self.last_claim: float | None = None
        self.processed: list[str] = []  # ids of the tasks this worker finished (for tests and logs)
        self.slots: list[asyncio.Task] = []

    async def heartbeat(self, task: dict) -> None:
        interval = max(1.0, task.get("leaseSeconds", 300) / 3)
        while True:
            await asyncio.sleep(interval)
            await self.client.heartbeat(task["id"])

    async def run_task(self, task: dict) -> None:
        handler = self.handlers.get(task["step"])
        beat = asyncio.create_task(self.heartbeat(task))
        self.in_flight[task["id"]] = task
        try:
            try:
                if handler is None:
                    raise StepError(f"no handler for {task['step']}", retryable=False)
                result = await handler(task, self.client)
            except StepError as exc:
                await self.client.fail(task["id"], str(exc), exc.retryable)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - an unexpected handler error is retried
                log.exception("task %s (%s) failed", task["id"], task["step"])
                await self.client.fail(task["id"], f"{type(exc).__name__}: {exc}", True)
                return
            try:
                await self.client.complete(task["id"], result or {})
            except ResultTooLarge as exc:
                await self.client.fail(task["id"], f"{task['step']} result too large: {exc}", False)
                return
            self.processed.append(task["id"])
        except LeaseLost:
            log.warning("task %s: lease lost, abandoned", task["id"])
        finally:
            beat.cancel()
            self.in_flight.pop(task["id"], None)

    async def slot(self) -> None:
        while not self.stopping.is_set():
            try:
                task = await self.client.claim()
            except Exception:  # noqa: BLE001 - the API may be restarting
                log.exception("claim failed")
                task = None
            if task is None:
                try:
                    await asyncio.wait_for(self.stopping.wait(), self.settings.worker_poll_interval)
                except asyncio.TimeoutError:
                    pass
                continue
            self.last_claim = time.time()
            try:
                await self.run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - e.g. the API restarting: the slot goes on, the lease recovers the task
                log.exception("task %s could not be reported; its lease will expire", task["id"])

    def start(self) -> None:
        self.slots = [asyncio.create_task(self.slot()) for _ in range(self.settings.worker_concurrency)]

    async def stop(self) -> None:
        """Stop claiming; give in-flight tasks the grace period, then fail them as retryable."""
        self.stopping.set()
        _, pending = await asyncio.wait(self.slots, timeout=self.settings.worker_shutdown_grace) \
            if self.slots else (set(), set())
        unfinished = list(self.in_flight.values())
        for slot in pending:
            slot.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in unfinished:
            try:
                await self.client.fail(task["id"], "worker shut down", True)
            except Exception:  # noqa: BLE001
                log.warning("task %s could not be released; its lease will expire", task["id"])
