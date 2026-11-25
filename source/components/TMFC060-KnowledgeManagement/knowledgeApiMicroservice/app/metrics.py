"""Prometheus metrics of the API, served on their own listener (the component's `metrics` management API).

Requests are counted and timed per method, route template and status, so ids in paths don't create new series.
The listener serves only ``/{component_name}/metrics`` (the ExposedAPI path, which the gateway does not rewrite).
"""
import asyncio
import logging
import time

import uvicorn
from prometheus_client import (CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, PlatformCollector,
                               ProcessCollector, generate_latest)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

log = logging.getLogger(__name__)


class Metrics:
    """The API's metrics in their own registry (one per app, so test apps don't share counters)."""

    def __init__(self):
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        self.requests = Counter("tmfc060_http_requests_total", "HTTP requests handled by the knowledge API",
                                ["method", "route", "status"], registry=self.registry)
        self.latency = Histogram("tmfc060_http_request_duration_seconds", "Time to answer an HTTP request",
                                 ["method", "route"], registry=self.registry)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.labels(method, route, str(status)).inc()
        self.latency.labels(method, route).observe(seconds)


def route_template(scope) -> str:
    """The matched route's path template (``/…/knowledge/{id}``); ``unmatched`` for 404s outside every route."""
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    if not template:
        return "unmatched"
    # A route of an included router knows only its own part of the path (FastAPI 0.165 no longer copies prefixes):
    # the router prefixes are the static part of the request path before what the route itself matched
    try:
        matched = template.format(**scope.get("path_params", {}))
    except (KeyError, IndexError, ValueError):
        return template
    path = scope.get("path", "")
    return path[:-len(matched)] + template if matched and path.endswith(matched) else template


class MetricsMiddleware:
    """Pure ASGI middleware: times each HTTP request and records it under its route template."""

    def __init__(self, app, metrics: Metrics):
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.perf_counter()
        status = 500

        async def send_status(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        finally:
            self.metrics.observe(scope["method"], route_template(scope), status, time.perf_counter() - start)


def build_app(metrics: Metrics, path: str) -> Starlette:
    async def serve(request: Request) -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    return Starlette(routes=[Route(path, serve)])


class MetricsServer:
    """The metrics listener, started and stopped with the API (off when metrics_port is negative)."""

    def __init__(self, metrics: Metrics, settings):
        self.metrics = metrics
        self.settings = settings
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task | None = None
        self.port: int | None = None

    @property
    def path(self) -> str:
        return f"/{self.settings.component_name}/metrics"

    async def start(self) -> None:
        if self.settings.metrics_port < 0:
            return
        config = uvicorn.Config(build_app(self.metrics, self.path), host=self.settings.metrics_host,
                                port=self.settings.metrics_port, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(config)
        self.server.install_signal_handlers = lambda: None
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            if self.task.done():
                self.task.result()
            await asyncio.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        log.info("metrics listening on %s:%s%s", self.settings.metrics_host, self.port, self.path)

    async def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
            await self.task
