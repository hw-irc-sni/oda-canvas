"""Add, change and remove LLM models in agentgateway's config (llm.models)."""

import json
import secrets
from typing import Any
from urllib.parse import urlsplit

import httpx
from kubernetes import client as k8s

from k8s.resources import ai_gateway_config as config_file
from k8s.resources.ai_gateway_config import as_dict
from k8s.resources.ai_gateways import GatewayRequestRefused, _mask_prose, parse_config_dump

FORM_PARAMS = (
    "model",
    "baseUrl",
    "awsRegion",
    "bedrockEndpointPreference",
    "vertexRegion",
    "vertexProject",
    "azureResourceName",
    "azureResourceType",
    "azureApiVersion",
    "azureProjectName",
    "tokenize",
)
FORM_FIELDS = {"name", "provider", "visibility", "passthrough", "params"}
# Settings that shape a request or who may make it, but not where it goes or how it's authenticated.
_FIELDS_THAT_KEEP_THE_UPSTREAM = {
    "id",
    "name",
    "visibility",
    "passthrough",
    "authorization",
    "defaults",
    "overrides",
    "transformation",
    "finalTransformation",
    "responseHeaders",
    "health",
    "promptCaching",
    "matches",
}


OPENAI_BASE_URL = "https://api.openai.com/v1"
MAX_UPSTREAM_LIST_BYTES = 4 * 1024 * 1024
MAX_UPSTREAM_MODELS = 5000
MAX_UPSTREAM_VALUE_LENGTH = 512
UPSTREAM_LIST_TIMEOUT_SECONDS = 10.0
# Providers whose model list isn't GET {baseUrl}/models with a bearer key.
NOT_OPENAI_COMPATIBLE = {"anthropic", "gemini", "vertex", "bedrock", "azure", "copilot"}


def _entries(config: config_file.GatewayConfig) -> list[Any]:
    return config.entries("llm", "models")


def _view(entry: dict[str, Any]) -> dict[str, Any]:
    params = as_dict(entry.get("params"))
    return {
        "name": entry.get("name"),
        "provider": entry.get("provider"),
        "visibility": entry.get("visibility"),
        "passthrough": entry.get("passthrough"),
        "params": {key: params[key] for key in FORM_PARAMS if key in params},
        "apiKeySet": params.get("apiKey") is not None,
        "advanced": {key: value for key, value in entry.items() if key not in FORM_FIELDS},
        "revision": config_file.revision(entry),
    }


def list_model_configs(core: k8s.CoreV1Api | None = None) -> dict[str, Any]:
    if core is None and not config_file.config_is_changeable():
        return {"configurable": False, "models": []}
    config = config_file.read_config(core or config_file.core_api())
    return {"configurable": True, "models": [_view(entry) for entry in _entries(config) if isinstance(entry, dict)]}


def _key_file_path(entry: dict[str, Any] | None) -> str | None:
    path = as_dict(as_dict(as_dict(entry).get("params")).get("apiKey")).get("file")
    return path if isinstance(path, str) else None


def _active_names(dump: Any) -> set[str]:
    return {model["name"] for model in parse_config_dump(dump)["models"]}


def _upstream_settings(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Everything that could change where a request goes, including settings added later."""
    settings = {key: value for key, value in as_dict(entry).items() if key not in _FIELDS_THAT_KEEP_THE_UPSTREAM}
    settings["params"] = {
        key: value for key, value in as_dict(settings.get("params")).items() if key not in ("model", "tokenize")
    }
    return settings


def _refuse_keeping_key_for_new_upstream(entry: dict[str, Any], existing: dict[str, Any] | None) -> None:
    if existing is None or _upstream_settings(entry) == _upstream_settings(existing):
        return
    old_key = as_dict(existing.get("params")).get("apiKey")
    kept_key = old_key is not None and as_dict(entry.get("params")).get("apiKey") == old_key
    kept_auth = existing.get("auth") is not None and entry.get("auth") == existing["auth"]
    if kept_key or kept_auth:
        raise GatewayRequestRefused(
            422, "This changes where requests go, so enter its API key and other credentials again or remove them."
        )
    config_file.refuse_keeping_secrets_for_new_upstream(entry, existing)


def _build_entry(
    request: dict[str, Any], existing: dict[str, Any] | None, key_file: str | None
) -> dict[str, Any]:
    clash = set(request["advanced"]) & FORM_FIELDS
    if clash:
        raise GatewayRequestRefused(422, f"Set {', '.join(sorted(clash))} in the form, not in the advanced settings.")
    provider = request["provider"]
    if not provider:
        raise GatewayRequestRefused(422, "A provider is required.")
    if isinstance(provider, dict) and (
        set(provider) not in ({"custom"}, {"reference"})
        or not (isinstance(provider.get("custom"), dict) or isinstance(provider.get("reference"), str))
    ):
        raise GatewayRequestRefused(422, "The provider must be a name, a custom provider or a reference.")
    base_url = request["params"].get("baseUrl")
    if base_url and urlsplit(base_url).scheme not in ("http", "https"):
        raise GatewayRequestRefused(422, "The base URL must start with http:// or https://.")
    if base_url:
        config_file.refuse_gateway_local_host(base_url)

    old_params = as_dict(as_dict(existing).get("params"))
    params = {key: value for key, value in old_params.items() if key not in FORM_PARAMS}
    params.update({key: value for key, value in request["params"].items() if value not in (None, "")})
    if key_file:
        params["apiKey"] = {"file": config_file.key_file_path(key_file)}
    elif request["removeApiKey"]:
        params.pop("apiKey", None)

    entry: dict[str, Any] = {"name": request["name"], "provider": provider}
    for field in ("visibility", "passthrough"):
        if request.get(field):
            entry[field] = request[field]
    if params:
        entry["params"] = params
    entry.update(request["advanced"])
    allowed_file = ("params", "apiKey", "file", config_file.key_file_path(key_file)) if key_file else None
    config_file.refuse_new_secrets(entry, existing, allowed_file)
    _refuse_keeping_key_for_new_upstream(entry, existing)
    return entry


def _save(
    request: dict[str, Any],
    original_name: str | None,
    revision: str | None,
    core: k8s.CoreV1Api,
    http: httpx.Client,
) -> dict[str, Any]:
    config = config_file.read_config(core)
    entries = _entries(config)
    existing = config_file.find_entry(entries, original_name) if original_name else None
    if original_name and existing is None:
        raise GatewayRequestRefused(404, "This model isn't in the gateway config any more.")
    if existing is not None and config_file.revision(existing) != revision:
        raise GatewayRequestRefused(409, "This model was changed after you opened it. Reopen it to see the changes.")
    other = config_file.find_entry(entries, request["name"])
    if other is not None and other is not existing:
        raise GatewayRequestRefused(409, "A model with this name already exists.")

    new_key = (secrets.token_hex(8), request["apiKey"]) if request["apiKey"] else None
    entry = _build_entry(request, existing, new_key[0] if new_key else None)
    if entry == existing:
        return {"applied": True, "message": "Nothing changed."}
    before = config_file.ready_to_change(http)

    if existing is None:
        entries.append(entry)
    else:
        entries[entries.index(existing)] = entry

    if existing is not None and request["name"] == original_name:
        applied = lambda dump: dump != before.dump
    else:
        applied = lambda dump: request["name"] in _active_names(dump)
    # A replaced or removed key is deleted only once the gateway has taken the change.
    old_key_file = config_file.unused_portal_key(_key_file_path(existing), config.document) if new_key or request["removeApiKey"] else None
    return config_file.change_config(core, http, config, before, applied, new_key, old_key_file)


def save_model(
    request: dict[str, Any],
    original_name: str | None = None,
    revision: str | None = None,
    core: k8s.CoreV1Api | None = None,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    return config_file.run_change(lambda api, client: _save(request, original_name, revision, api, client), core, http)


def _remove(name: str, core: k8s.CoreV1Api, http: httpx.Client) -> dict[str, Any]:
    config = config_file.read_config(core)
    entries = _entries(config)
    existing = config_file.find_entry(entries, name)
    if existing is None:
        raise GatewayRequestRefused(404, "This model isn't in the gateway config any more.")
    before = config_file.ready_to_change(http)
    entries.remove(existing)
    old_key = config_file.unused_portal_key(_key_file_path(existing), config.document)
    return config_file.change_config(
        core, http, config, before, lambda dump: name not in _active_names(dump), None, old_key
    )


def remove_model(name: str, core: k8s.CoreV1Api | None = None, http: httpx.Client | None = None) -> dict[str, Any]:
    return config_file.run_change(lambda api, client: _remove(name, api, client), core, http)


def _upstream_value(value: Any) -> Any:
    """A value for one cell of the model table: scalars as they are, anything else as short JSON."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = _mask_prose(text)
    return text if len(text) <= MAX_UPSTREAM_VALUE_LENGTH else text[: MAX_UPSTREAM_VALUE_LENGTH - 1] + "…"


def _upstream_refusal(response: httpx.Response, sent_key: bool) -> GatewayRequestRefused:
    # The body isn't shown: some providers echo part of the key in it.
    if response.status_code in (401, 403) and not sent_key:
        return GatewayRequestRefused(
            502, f"The provider needs an API key (HTTP {response.status_code}). Enter it to list the models."
        )
    if response.status_code in (401, 403):
        return GatewayRequestRefused(502, f"The provider refused the API key (HTTP {response.status_code}).")
    if response.status_code == 404:
        return GatewayRequestRefused(502, "The provider has no model list at this base URL (HTTP 404).")
    if response.is_redirect:
        return GatewayRequestRefused(502, f"The provider redirected the request (HTTP {response.status_code}). Check the base URL.")
    return GatewayRequestRefused(502, f"The provider couldn't list its models (HTTP {response.status_code}).")


def _read_limited(response: httpx.Response) -> bytes:
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > MAX_UPSTREAM_LIST_BYTES:
            raise GatewayRequestRefused(502, "The provider's model list is too large.")
    return bytes(body)


def list_upstream_models(
    provider: str | dict[str, Any], base_url: str | None, api_key: str | None, http: httpx.Client | None = None
) -> dict[str, Any]:
    """The models an OpenAI-compatible provider offers, from GET {baseUrl}/models.

    Only a key typed in the form is used. The stored key stays in its Secret, which the portal can't read.
    """
    if isinstance(provider, str) and provider in NOT_OPENAI_COMPATIBLE:
        raise GatewayRequestRefused(422, f"Listing the models of {provider} isn't supported.")
    base_url = (base_url or "").strip()
    if not base_url:
        if provider != "openAI":
            raise GatewayRequestRefused(422, "Enter the base URL to list this provider's models.")
        base_url = OPENAI_BASE_URL
    if urlsplit(base_url).scheme not in ("http", "https"):
        raise GatewayRequestRefused(422, "The base URL must start with http:// or https://.")
    config_file.refuse_gateway_local_host(base_url)

    headers = {"Accept": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    url = base_url.rstrip("/") + "/models"
    try:
        if http is not None:
            body = _fetch(http, url, headers)
        else:
            # Redirects aren't followed, so a provider can't send the key, or the request, somewhere else.
            with httpx.Client(timeout=UPSTREAM_LIST_TIMEOUT_SECONDS, follow_redirects=False) as owned:
                body = _fetch(owned, url, headers)
    except httpx.TimeoutException as exc:
        raise GatewayRequestRefused(504, "The provider didn't answer in time.") from exc
    except httpx.HTTPError as exc:
        reason = _mask_prose(str(exc) or type(exc).__name__)[:200]
        raise GatewayRequestRefused(502, f"Couldn't reach the provider: {reason}") from exc
    try:
        document = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise GatewayRequestRefused(502, "The provider's model list isn't JSON.") from exc
    items = document.get("data") if isinstance(document, dict) else document
    if not isinstance(items, list):
        raise GatewayRequestRefused(502, "The provider's answer has no model list.")

    models: list[dict[str, Any]] = []
    for item in items[:MAX_UPSTREAM_MODELS]:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        model_id = item.get("id", item.get("name"))
        if not isinstance(model_id, str) or not model_id:
            continue
        row = {str(key): _upstream_value(value) for key, value in item.items()}
        row["id"] = model_id
        models.append(row)
    models.sort(key=lambda row: row["id"].lower())
    return {
        "baseUrl": base_url,
        "models": models,
        "truncated": len(items) > MAX_UPSTREAM_MODELS,
    }


def _fetch(http: httpx.Client, url: str, headers: dict[str, str]) -> bytes:
    with http.stream("GET", url, headers=headers) as response:
        if response.status_code != 200:
            raise _upstream_refusal(response, "Authorization" in headers)
        return _read_limited(response)
