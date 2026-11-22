"""python -m worker: the claim loop and the health server, until SIGTERM."""
import asyncio
import logging
import signal

import uvicorn

from .client import InternalClient
from .config import Settings
from .health import health_app
from .loop import Worker


async def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = InternalClient(settings.internal_api_url, settings.internal_token_file, settings.worker_id,
                            settings.worker_request_timeout)
    worker = Worker(settings, client)
    worker.start()
    server = uvicorn.Server(uvicorn.Config(health_app(worker), host=settings.worker_health_host,
                                           port=settings.worker_health_port, log_level="warning", lifespan="off"))
    server.install_signal_handlers = lambda: None
    serving = asyncio.create_task(server.serve())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await worker.stop()
    server.should_exit = True
    await serving
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
