"""The setup and teardown modes of the hook Job, against the fake Keycloak and a fake Secret API."""

import asyncio

import pytest
from kubernetes.client.exceptions import ApiException

import kc_setup
from fake_keycloak import FakeKeycloak, Running


class FakeCore:
    """The three CoreV1Api Secret calls kc_setup makes."""

    def __init__(self):
        self.secrets = {}

    def read_namespaced_secret(self, name, namespace):
        if (namespace, name) not in self.secrets:
            raise ApiException(status=404)
        return self.secrets[(namespace, name)]

    def create_namespaced_secret(self, namespace, body):
        self.secrets[(namespace, body.metadata.name)] = body

    def replace_namespaced_secret(self, name, namespace, body):
        self.secrets[(namespace, name)] = body

    def delete_namespaced_secret(self, name, namespace):
        if self.secrets.pop((namespace, name), None) is None:
            raise ApiException(status=404)


def cfg(base, **extra):
    env = {"KEYCLOAK_URL": base, "KC_ADMIN_PASSWORD": "admin-pw", "POD_NAMESPACE": "canvas",
           "SETUP_TIMEOUT_SECONDS": "0", "SETUP_RETRY_SECONDS": "0"}
    env.update(extra)
    return kc_setup.Config(env)


def run_mode(fake, mode, core, **extra):
    logs = []

    async def go():
        async with Running(fake) as base:
            return await kc_setup.run(mode, cfg(base, **extra), core, log=logs.append), base

    rc, base = asyncio.run(go())
    return rc, logs, base


def test_setup_creates_client_roles_and_secret():
    fake, core = FakeKeycloak(), FakeCore()
    rc, logs, base = run_mode(fake, "setup", core)
    assert rc == 0
    client = fake.by_client_id("dataresource-operator")
    assert client["serviceAccountsEnabled"] is True
    assert fake.mapped("dataresource-operator", "realm-management") == ["manage-users", "view-clients"]
    s = core.secrets[("canvas", "dataresource-operator-oidc-secret")]
    assert s.string_data == {"client_id": "dataresource-operator", "client_secret": client["secret"],
                             "token_url": f"{base}/realms/odari/protocol/openid-connect/token"}
    assert s.metadata.labels["oda.tmforum.org/operatorId"] == "TMFOP012"
    assert any("created client" in line for line in logs)
    assert not any(client["secret"] in line for line in logs)


def test_setup_is_idempotent():
    fake, core = FakeKeycloak(), FakeCore()
    run_mode(fake, "setup", core)
    secret = fake.by_client_id("dataresource-operator")["secret"]
    rc, logs, _ = run_mode(fake, "setup", core)
    assert rc == 0
    assert sum(c["clientId"] == "dataresource-operator" for c in fake.clients.values()) == 1
    assert core.secrets[("canvas", "dataresource-operator-oidc-secret")].string_data["client_secret"] == secret
    assert any("exists" in line for line in logs) and any("updated" in line for line in logs)


def test_setup_fails_after_timeout_without_printing_the_password():
    fake, core = FakeKeycloak(admin_password="other"), FakeCore()
    rc, logs, _ = run_mode(fake, "setup", core)
    assert rc == 1 and core.secrets == {}
    assert any("admin login refused with HTTP 401" in line for line in logs)
    assert not any("admin-pw" in line for line in logs)


def test_setup_retries_until_keycloak_is_up():
    fake, core = FakeKeycloak(), FakeCore()
    fake.down = True
    calls = []

    async def sleep(_):
        calls.append(1)
        fake.down = False            # Keycloak comes up during the wait

    async def go():
        async with Running(fake) as base:
            c = cfg(base, SETUP_TIMEOUT_SECONDS="600")
            return await kc_setup.run("setup", c, core, log=lambda _: None, sleep=sleep)

    assert asyncio.run(go()) == 0 and calls == [1]
    assert ("canvas", "dataresource-operator-oidc-secret") in core.secrets


def test_teardown_deletes_client_and_secret_and_never_fails():
    fake, core = FakeKeycloak(), FakeCore()
    run_mode(fake, "setup", core)
    rc, _, _ = run_mode(fake, "teardown", core)
    assert rc == 0 and fake.by_client_id("dataresource-operator") is None and core.secrets == {}
    rc, _, _ = run_mode(fake, "teardown", core)            # nothing left: still fine
    assert rc == 0
    fake.down = True
    rc, logs, _ = run_mode(fake, "teardown", core)
    assert rc == 0 and any("teardown incomplete" in line for line in logs)


def test_config_requires_url_and_password():
    with pytest.raises(kc_setup.SetupError):
        kc_setup.Config({"KEYCLOAK_URL": "http://kc/auth"})
    with pytest.raises(kc_setup.SetupError):
        kc_setup.Config({"KC_ADMIN_PASSWORD": "x"})
