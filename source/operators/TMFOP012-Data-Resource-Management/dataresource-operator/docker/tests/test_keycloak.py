"""TokenManager and KeycloakAdmin against the fake Keycloak."""

import asyncio

import aiohttp
import pytest

from fake_keycloak import FakeKeycloak, Running
from keycloak import CredentialsMissing, KeycloakAdmin, KeycloakError, TokenManager, read_credentials


def write_oidc(path, client_id, secret, token_url):
    path.mkdir(parents=True, exist_ok=True)
    (path / "client_id").write_text(client_id)
    (path / "client_secret").write_text(secret)
    (path / "token_url").write_text(token_url)


def operator_client(fake):
    """A dataresource-operator client with view-clients + manage-users, as the setup Job leaves it."""
    cid = fake.add_client("dataresource-operator", secret="op-secret")
    rm = fake.by_client_id("realm-management")["id"]
    fake.sa_of(cid)["mappings"][rm] = {"view-clients", "manage-users"}
    return cid


def run(coro):
    return asyncio.run(coro)


def test_read_credentials_missing(tmp_path):
    with pytest.raises(CredentialsMissing):
        read_credentials(str(tmp_path / "absent"))
    write_oidc(tmp_path / "oidc", "c", "", "http://x/token")
    with pytest.raises(CredentialsMissing, match="client_secret"):
        read_credentials(str(tmp_path / "oidc"))


def test_token_cached_until_near_expiry(tmp_path):
    fake = FakeKeycloak(expires_in=100)
    operator_client(fake)
    now = [0.0]

    async def go():
        async with Running(fake) as base, aiohttp.ClientSession() as s:
            write_oidc(tmp_path, "dataresource-operator", "op-secret", f"{base}/realms/odari/protocol/openid-connect/token")
            tm = TokenManager(str(tmp_path), lambda: s, refresh_buffer=30, clock=lambda: now[0])
            t1 = await tm.token()
            now[0] = 60          # 40 s left, more than the 30 s buffer
            t2 = await tm.token()
            now[0] = 75          # 25 s left: refreshed
            t3 = await tm.token()
            tm.invalidate()
            t4 = await tm.token()
            return t1, t2, t3, t4

    t1, t2, t3, t4 = run(go())
    assert t1 == t2 and t3 != t2 and t4 != t3
    assert fake.token_requests == 3


def test_token_refused_and_secret_picked_up_later(tmp_path):
    fake = FakeKeycloak()
    operator_client(fake)

    async def go():
        async with Running(fake) as base, aiohttp.ClientSession() as s:
            url = f"{base}/realms/odari/protocol/openid-connect/token"
            tm = TokenManager(str(tmp_path / "oidc"), lambda: s)
            with pytest.raises(CredentialsMissing):
                await tm.token()
            write_oidc(tmp_path / "oidc", "dataresource-operator", "wrong", url)
            with pytest.raises(KeycloakError) as e:
                await tm.token()
            assert e.value.status == 401 and "wrong" not in str(e.value)
            write_oidc(tmp_path / "oidc", "dataresource-operator", "op-secret", url)
            return await tm.token()

    assert run(go()).startswith("tok-")


def test_admin_role_mapping_round_trip(tmp_path):
    """The S4 flow: self-grant of the provider's canvasRole, grant and revoke on a consumer, no escalation."""
    fake = FakeKeycloak()
    op = operator_client(fake)
    prov = fake.add_client("km-knowledgemanagement")
    for r in ("canvasRole", "knowledge-viewer", "knowledge-editor"):
        fake.add_role(prov, r)
    fake.add_client("assurance-agent")

    async def go():
        async with Running(fake) as base, aiohttp.ClientSession() as s:
            write_oidc(tmp_path, "dataresource-operator", "op-secret", f"{base}/realms/odari/protocol/openid-connect/token")
            kc = KeycloakAdmin(base, "odari", TokenManager(str(tmp_path), lambda: s), lambda: s)
            p = await kc.find_client("km-knowledgemanagement")
            assert await kc.find_client("no-such-client") is None
            assert await kc.client_role(p["id"], "no-such-role") is None
            me = await kc.service_account_user(op)
            await kc.map_client_roles(me["id"], p["id"], [await kc.client_role(p["id"], "canvasRole")])
            consumer = await kc.find_client("assurance-agent")
            sa = await kc.service_account_user(consumer["id"])
            viewer = await kc.client_role(p["id"], "knowledge-viewer")
            await kc.map_client_roles(sa["id"], p["id"], [viewer])
            granted = [r["name"] for r in await kc.mapped_client_roles(sa["id"], p["id"])]
            await kc.unmap_client_roles(sa["id"], p["id"], [viewer])
            revoked = await kc.mapped_client_roles(sa["id"], p["id"])
            rm = await kc.find_client("realm-management")
            with pytest.raises(KeycloakError) as e:
                await kc.map_client_roles(me["id"], rm["id"], [await kc.client_role(rm["id"], "manage-realm")])
            return granted, revoked, e.value.status

    granted, revoked, status = run(go())
    assert fake.mapped("dataresource-operator", "km-knowledgemanagement") == ["canvasRole"]
    assert granted == ["knowledge-viewer"] and revoked == [] and status == 403
