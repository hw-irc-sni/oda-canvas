"""Read-only view of the solution packages held by the OpenAN Orchestration Center.

Each solution package is a PSOP workflow tagged ``solution-package``. The workflow
API only accepts a user session, so the backend logs in with the configured account
and keeps the token server-side. The password is read from a Kubernetes Secret on
each login, so a regenerated password is picked up without a restart.

Upstream failures are returned as 502 with fixed text, never 401, so they cannot
log the portal user out.
"""
import asyncio
import base64
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends
from kubernetes import client as k8s_client
from kubernetes.client.exceptions import ApiException

from auth.middleware import get_current_user
from auth.models import AuthUser
from config import get_settings
from k8s.client import get_base_api_client
from utils.exceptions import CanvasPortalError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/solution-packages", tags=["solution-packages"])

TOKEN_PATH = "/api/v1/auth/token"
WORKFLOWS_PATH = "/rest/v1/orchestrate/workflows"
PACKAGE_TAG = "solution-package"
PAGE_LIMIT = 100
MAX_PAGES = 20
DETAIL_CONCURRENCY = 4
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
CACHE_TTL_SECONDS = 60.0
MAX_TOKEN_SECONDS = 3600
# The Orchestration Center allows 5 logins a minute per client, successful ones
# included, so after a rejected login the backend waits before trying again.
LOGIN_BACKOFF_SECONDS = 60.0
SUMMARY_MAX_CHARS = 600


class OrchestrationCenterError(CanvasPortalError):
    """A fixed message for the browser. Upstream content is only logged."""

    def __init__(self, message: str):
        super().__init__(message, 502)


class _State:
    token: Optional[str] = None
    token_expires_at: float = 0.0
    login_blocked_until: float = 0.0
    result: Optional[dict] = None
    result_expires_at: float = 0.0


_state = _State()
_lock = asyncio.Lock()


def _base_url() -> str:
    return get_settings().ORCHESTRATION_CENTER_URL.rstrip("/")


def _read_password() -> str:
    settings = get_settings()
    name = settings.ORCHESTRATION_CENTER_PASSWORD_SECRET_NAME
    if not name:
        if settings.ORCHESTRATION_CENTER_PASSWORD:
            return settings.ORCHESTRATION_CENTER_PASSWORD
        raise OrchestrationCenterError("The Orchestration Center credentials are not configured.")

    namespace = settings.ORCHESTRATION_CENTER_PASSWORD_SECRET_NAMESPACE
    key = settings.ORCHESTRATION_CENTER_PASSWORD_SECRET_KEY
    try:
        secret = k8s_client.CoreV1Api(get_base_api_client()).read_namespaced_secret(name, namespace)
    except ApiException as exc:
        logger.warning("Cannot read Orchestration Center secret %s/%s: HTTP %s", namespace, name, exc.status)
        raise OrchestrationCenterError("The portal cannot read the Orchestration Center credentials.") from exc
    encoded = (secret.data or {}).get(key)
    if not encoded:
        logger.warning("Orchestration Center secret %s/%s has no key %s", namespace, name, key)
        raise OrchestrationCenterError("The Orchestration Center credentials are missing.")
    return base64.b64decode(encoded).decode()


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (ValueError, RecursionError):
        return None


async def _login(http: httpx.AsyncClient) -> str:
    now = time.monotonic()
    if now < _state.login_blocked_until:
        raise OrchestrationCenterError("The Orchestration Center rejected the login. Retrying shortly.")

    password = await asyncio.to_thread(_read_password)
    try:
        response = await http.post(
            TOKEN_PATH,
            json={"username": get_settings().ORCHESTRATION_CENTER_USERNAME, "password": password},
        )
    except httpx.HTTPError as exc:
        logger.warning("Orchestration Center login failed: %s", type(exc).__name__)
        raise OrchestrationCenterError("The Orchestration Center could not be reached.") from exc

    if response.status_code in (401, 403, 429):
        _state.login_blocked_until = now + LOGIN_BACKOFF_SECONDS
        logger.warning("Orchestration Center login returned HTTP %s", response.status_code)
        raise OrchestrationCenterError("The Orchestration Center rejected the login. Retrying shortly.")
    body = _json(response)
    token = body.get("access_token") if isinstance(body, dict) else None
    if not response.is_success or not isinstance(token, str) or not token:
        logger.warning("Orchestration Center login returned HTTP %s without a token", response.status_code)
        raise OrchestrationCenterError("The Orchestration Center returned an unexpected response.")

    expires_in = body.get("expires_in")
    lifetime = expires_in if isinstance(expires_in, (int, float)) and expires_in > 0 else 300
    _state.token = token
    _state.token_expires_at = now + min(lifetime, MAX_TOKEN_SECONDS) - 30
    return token


async def _get(http: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> dict:
    """GET an enveloped response, logging in again once if the session was dropped."""
    for attempt in range(2):
        if not _state.token or time.monotonic() >= _state.token_expires_at:
            await _login(http)
        try:
            response = await http.get(path, params=params, headers={"Authorization": f"Bearer {_state.token}"})
        except httpx.HTTPError as exc:
            logger.warning("Orchestration Center GET %s failed: %s", path, type(exc).__name__)
            raise OrchestrationCenterError("The Orchestration Center could not be reached.") from exc
        # Sessions live in the Orchestration Center's memory and end when its pod restarts.
        if response.status_code == 401 and attempt == 0:
            _state.token = None
            continue
        body = _json(response)
        if not response.is_success or not isinstance(body, dict):
            logger.warning("Orchestration Center GET %s returned HTTP %s", path, response.status_code)
            raise OrchestrationCenterError("The Orchestration Center returned an unexpected response.")
        return body
    raise OrchestrationCenterError("The Orchestration Center rejected the session.")


def _text(value: Any, limit: int = 300) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _summary(intent: Any) -> str:
    """The first prose paragraph of the source chapter, without its heading."""
    if not isinstance(intent, str):
        return ""
    for paragraph in re.split(r"\n\s*\n", intent):
        lines = [line for line in paragraph.strip().splitlines() if not line.lstrip().startswith(("#", "[", "!", "|"))]
        text = " ".join(" ".join(lines).split())
        if text:
            return text if len(text) <= SUMMARY_MAX_CHARS else text[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return ""


def _steps(raw: Any) -> list[dict]:
    steps = []
    for step in raw if isinstance(raw, list) else []:
        if not isinstance(step, dict):
            continue
        tasks = [
            {
                "agent": _text(task.get("agent"), 120),
                "skill": _text(task.get("skill"), 120),
                "description": _text(task.get("description"), 500),
            }
            for task in step.get("subtasks") or []
            if isinstance(task, dict)
        ]
        following = [_text(n.get("step"), 64) for n in step.get("next") or [] if isinstance(n, dict) and n.get("step")]
        steps.append({"name": _text(step.get("name"), 64), "type": _text(step.get("type"), 32), "next": following, "tasks": tasks})
    return steps


def _package(summary: dict, detail: dict) -> dict:
    steps = _steps(detail.get("steps"))
    tasks = [task for step in steps for task in step["tasks"]]
    return {
        "id": _text(summary.get("workflow_id"), 64),
        "name": _text(summary.get("name"), 200),
        "summary": _summary(detail.get("user_intent")),
        "createdAt": _text(summary.get("created_at"), 40) or None,
        "tags": [_text(t, 64) for t in summary.get("tags") or [] if isinstance(t, str)],
        "agents": sorted({t["agent"] for t in tasks if t["agent"]}),
        "skills": sorted({t["skill"] for t in tasks if t["skill"]}),
        "steps": steps,
    }


async def _fetch() -> list[dict]:
    async with httpx.AsyncClient(base_url=_base_url(), timeout=TIMEOUT, follow_redirects=False) as http:
        summaries: list[dict] = []
        for page in range(MAX_PAGES):
            body = await _get(http, WORKFLOWS_PATH, {"limit": PAGE_LIMIT, "offset": page * PAGE_LIMIT})
            data = body.get("data")
            if not isinstance(data, list):
                raise OrchestrationCenterError("The Orchestration Center returned an unexpected response.")
            summaries.extend(item for item in data if isinstance(item, dict))
            if not (body.get("pagination") or {}).get("has_more"):
                break

        packages = [
            s for s in summaries
            if PACKAGE_TAG in (s.get("tags") or []) and isinstance(s.get("workflow_id"), str)
        ]
        limit = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def detail(summary: dict) -> dict:
            async with limit:
                body = await _get(http, f"{WORKFLOWS_PATH}/{summary['workflow_id']}")
            data = body.get("data")
            return _package(summary, data if isinstance(data, dict) else {})

        results = await asyncio.gather(*(detail(s) for s in packages))
    return sorted(results, key=lambda p: p["name"])


@router.get("")
async def list_solution_packages(_: AuthUser = Depends(get_current_user)):
    settings = get_settings()
    page_url = settings.ORCHESTRATION_CENTER_PAGE_URL or None
    if not settings.ORCHESTRATION_CENTER_URL:
        return {"configured": False, "pageUrl": page_url, "fetchedAt": None, "packages": []}

    async with _lock:
        if _state.result is None or time.monotonic() >= _state.result_expires_at:
            packages = await _fetch()
            _state.result = {
                "configured": True,
                "pageUrl": page_url,
                "fetchedAt": datetime.now(UTC).isoformat(),
                "packages": packages,
            }
            _state.result_expires_at = time.monotonic() + CACHE_TTL_SECONDS
        return _state.result
