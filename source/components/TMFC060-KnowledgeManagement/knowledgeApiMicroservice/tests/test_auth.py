"""Token validation (spike S1 rules) and the OAS-driven role guards."""
import httpx
import pytest
from fastapi import Depends

from app.auth import Principal, require_operation
from app.config import Settings
from app.main import create_app

from .tokens import CLIENT, INTERNAL_ISSUER, ISSUER, KeyPair, StaticJWKClient

KEY = KeyPair("key-1")


def make_app(**overrides):
    settings = Settings(component_name=CLIENT, keycloak_jwks_url="http://jwks.invalid/certs",
                        keycloak_issuers=[ISSUER, INTERNAL_ISSUER], **overrides)
    jwks = StaticJWKClient(KEY)
    from app.auth import TokenValidator
    app = create_app(settings, TokenValidator(settings, jwks))
    # Probe routes standing in for real resources until they are implemented: one read, one write.
    read = require_operation("listKnowledge")
    write = require_operation("createKnowledge")

    @app.get(settings.base_path + "/knowledge")
    async def list_knowledge(principal: Principal = Depends(read)):
        return {"user": principal.username, "roles": sorted(principal.roles)}

    @app.post(settings.base_path + "/knowledge")
    async def create_knowledge(principal: Principal = Depends(write)):
        return {"ok": True}

    return app, settings, jwks


async def call(app, method, path, token=None, headers=None, **kw):
    headers = {**(headers or {}), **({"Authorization": f"Bearer {token}"} if token else {})}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers=headers, **kw)


@pytest.fixture
def api():
    app, settings, jwks = make_app()
    return app, settings.base_path + "/knowledge", jwks


async def test_valid_token_with_role_is_allowed(api):
    app, path, _ = api
    response = await call(app, "GET", path, KEY.token(["knowledge-viewer"]))
    assert response.status_code == 200
    assert response.json() == {"user": "alice", "roles": ["knowledge-viewer"]}


async def test_in_cluster_issuer_is_accepted(api):
    app, path, _ = api
    assert (await call(app, "GET", path, KEY.token(iss=INTERNAL_ISSUER))).status_code == 200


@pytest.mark.parametrize("token_kwargs, reason", [
    ({"exp_in": -120}, "expired"),
    ({"iss": "https://evil.example/realms/odari"}, "issuer"),
    ({"aud": ["other-component", "account"]}, "audience"),
    ({"alg": "HS256"}, "algorithm"),
])
async def test_invalid_tokens_get_401(api, token_kwargs, reason):
    app, path, _ = api
    response = await call(app, "GET", path, KEY.token(**token_kwargs))
    assert response.status_code == 401, reason
    body = response.json()
    assert body["@type"] == "Error" and body["code"] == "401" and body["status"] == "401"
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_missing_and_malformed_tokens_get_401(api):
    app, path, _ = api
    assert (await call(app, "GET", path)).status_code == 401
    assert (await call(app, "GET", path, "not.a.jwt")).status_code == 401


async def test_token_signed_by_an_unknown_key_gets_401(api):
    app, path, _ = api
    assert (await call(app, "GET", path, KeyPair("other-key").token())).status_code == 401


async def test_expiry_within_leeway_is_accepted(api):
    app, path, _ = api
    assert (await call(app, "GET", path, KEY.token(exp_in=-10))).status_code == 200


async def test_missing_role_gets_403_with_the_needed_roles(api):
    app, path, _ = api
    response = await call(app, "POST", path, KEY.token(["knowledge-viewer"]))
    assert response.status_code == 403
    body = response.json()
    assert body["reason"] == "Forbidden" and "knowledge-editor" in body["message"]
    assert body["instance"] == path


async def test_roles_of_another_client_do_not_count(api):
    app, path, _ = api
    token = KEY.token(extra={"resource_access": {"other": {"roles": ["knowledge-editor"]}}})
    assert (await call(app, "POST", path, token)).status_code == 403


async def test_canvas_system_role_name_maps_to_canvasSystemRole(api):
    app, path, _ = api
    assert (await call(app, "POST", path, KEY.token(["canvasRole"]))).status_code == 200
    assert (await call(app, "POST", path, KEY.token(["canvasSystemRole"]))).status_code == 403


async def test_problem_json_is_served_when_asked(api):
    app, path, _ = api
    response = await call(app, "GET", path, headers={"Accept": "application/problem+json"})
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_jwks_is_cached_and_refetched_for_an_unknown_kid(api):
    app, path, jwks = api
    await call(app, "GET", path, KEY.token())
    await call(app, "GET", path, KEY.token())
    assert jwks.fetches == 1
    rotated = KeyPair("key-2")
    jwks.keys.append(rotated)
    assert (await call(app, "GET", path, rotated.token())).status_code == 200
    assert jwks.fetches == 2


async def test_auth_disabled_grants_every_role(monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    app, settings, _ = make_app(auth_enabled=False)
    response = await call(app, "POST", settings.base_path + "/knowledge")
    assert response.status_code == 200


def test_auth_disabled_is_refused_in_kubernetes(monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    with pytest.raises(RuntimeError, match="refused in Kubernetes"):
        create_app(Settings(component_name=CLIENT, auth_enabled=False))


def test_enabled_auth_needs_jwks_and_issuers():
    with pytest.raises(RuntimeError, match="KEYCLOAK_JWKS_URL"):
        create_app(Settings(component_name=CLIENT))


def test_issuers_are_read_comma_separated_from_the_environment(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUERS", f"{ISSUER}, {INTERNAL_ISSUER}")
    assert Settings().keycloak_issuers == [ISSUER, INTERNAL_ISSUER]
