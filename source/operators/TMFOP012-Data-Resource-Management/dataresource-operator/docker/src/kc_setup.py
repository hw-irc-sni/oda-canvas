"""Keycloak setup for TMFOP012, run by the chart's hook Jobs (spike S4, hw-irc-sni/oda-canvas#61).

    python kc_setup.py setup      # post-install, post-upgrade
    python kc_setup.py teardown   # pre-delete

setup, with the Keycloak admin credentials (from the canvas-keycloak Secret, only in this Job):
  1. creates the confidential client OPERATOR_CLIENT_ID (service account on, no browser or password flows) if missing;
  2. maps realm-management view-clients and manage-users to its service account (the narrowest built-in roles that
     allow mapping client roles on Keycloak 20);
  3. creates or updates the Secret OIDC_SECRET_NAME in POD_NAMESPACE with client_id, client_secret and token_url.
It is idempotent and retries while Keycloak or the realm is not up yet (SETUP_TIMEOUT_SECONDS).

teardown deletes the client and the Secret; it never fails, so an uninstall is not blocked.

The client secret is never printed.
"""

import asyncio
import os
import sys
import time
from urllib.parse import quote

import aiohttp

REALM_MANAGEMENT_ROLES = ("view-clients", "manage-users")


class SetupError(Exception):
    pass


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.keycloak_url = env.get("KEYCLOAK_URL", "").rstrip("/")
        self.realm = env.get("KEYCLOAK_REALM", "odari")
        self.admin_user = env.get("KC_ADMIN_USER", "admin")
        self.admin_password = env.get("KC_ADMIN_PASSWORD", "")
        self.client_id = env.get("OPERATOR_CLIENT_ID", "dataresource-operator")
        self.secret_name = env.get("OIDC_SECRET_NAME", "dataresource-operator-oidc-secret")
        self.namespace = env.get("POD_NAMESPACE", "canvas")
        self.timeout = float(env.get("SETUP_TIMEOUT_SECONDS", "600"))
        self.retry_delay = float(env.get("SETUP_RETRY_SECONDS", "10"))
        if not self.keycloak_url or not self.admin_password:
            raise SetupError("KEYCLOAK_URL and KC_ADMIN_PASSWORD are required")

    @property
    def token_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/token"


class Admin:
    """Keycloak admin calls with a master-realm admin token."""

    def __init__(self, cfg: Config, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self.base = f"{cfg.keycloak_url}/admin/realms/{cfg.realm}"
        self.token = None

    async def login(self) -> None:
        form = {"grant_type": "password", "client_id": "admin-cli",
                "username": self.cfg.admin_user, "password": self.cfg.admin_password}
        async with self.session.post(f"{self.cfg.keycloak_url}/realms/master/protocol/openid-connect/token",
                                     data=form) as r:
            if r.status != 200:
                raise SetupError(f"admin login refused with HTTP {r.status}")
            self.token = (await r.json())["access_token"]

    async def call(self, method, path, body=None, ok=(200, 201, 204)):
        async with self.session.request(method, self.base + path, json=body,
                                        headers={"Authorization": f"Bearer {self.token}"}) as r:
            if r.status == 404:
                return None
            if r.status not in ok:
                raise SetupError(f"{method} {path.split('?')[0]} refused with HTTP {r.status}")
            if r.status in (201, 204) or r.content_length == 0:
                return {}
            return await r.json()

    async def find_client(self, client_id):
        for c in await self.call("GET", f"/clients?clientId={quote(client_id)}&search=false") or []:
            if c.get("clientId") == client_id:
                return c
        return None


def write_secret(core, cfg: Config, client_secret: str) -> str:
    """Create or replace the credentials Secret; returns "created" or "updated"."""
    from kubernetes import client as k8s
    from kubernetes.client.exceptions import ApiException

    body = k8s.V1Secret(
        metadata=k8s.V1ObjectMeta(
            name=cfg.secret_name,
            labels={"app.kubernetes.io/part-of": "dataresource-operator",
                    "oda.tmforum.org/operatorId": "TMFOP012"}),
        type="Opaque",
        string_data={"client_id": cfg.client_id, "client_secret": client_secret, "token_url": cfg.token_url},
    )
    try:
        core.read_namespaced_secret(cfg.secret_name, cfg.namespace)
    except ApiException as e:
        if e.status != 404:
            raise
        core.create_namespaced_secret(cfg.namespace, body)
        return "created"
    core.replace_namespaced_secret(cfg.secret_name, cfg.namespace, body)
    return "updated"


async def setup_once(cfg: Config, session, core, log=print) -> None:
    kc = Admin(cfg, session)
    await kc.login()
    client = await kc.find_client(cfg.client_id)
    if client is None:
        await kc.call("POST", "/clients", {
            "clientId": cfg.client_id,
            "name": "Data Resource Management Operator (TMFOP012)",
            "protocol": "openid-connect",
            "publicClient": False,
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": False,
            "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": False,
        })
        client = await kc.find_client(cfg.client_id)
        if client is None:
            raise SetupError(f"client {cfg.client_id} not found after creation")
        log(f"created client {cfg.client_id}")
    else:
        log(f"client {cfg.client_id} exists")
    secret = await kc.call("GET", f"/clients/{client['id']}/client-secret")
    if not secret or not secret.get("value"):
        raise SetupError(f"client {cfg.client_id} has no secret")
    sa = await kc.call("GET", f"/clients/{client['id']}/service-account-user")
    if not sa:
        raise SetupError(f"client {cfg.client_id} has no service account")
    rm = await kc.find_client("realm-management")
    if rm is None:
        raise SetupError("realm-management client not found")
    roles = []
    for name in REALM_MANAGEMENT_ROLES:
        role = await kc.call("GET", f"/clients/{rm['id']}/roles/{name}")
        if role is None:
            raise SetupError(f"realm-management role {name} not found")
        roles.append(role)
    await kc.call("POST", f"/users/{sa['id']}/role-mappings/clients/{rm['id']}", roles)
    log(f"service account mapped to realm-management {', '.join(REALM_MANAGEMENT_ROLES)}")
    action = write_secret(core, cfg, secret["value"])
    log(f"secret {cfg.namespace}/{cfg.secret_name} {action}")


async def teardown_once(cfg: Config, session, core, log=print) -> None:
    from kubernetes.client.exceptions import ApiException

    kc = Admin(cfg, session)
    await kc.login()
    client = await kc.find_client(cfg.client_id)
    if client is not None:
        await kc.call("DELETE", f"/clients/{client['id']}")
        log(f"deleted client {cfg.client_id}")
    try:
        core.delete_namespaced_secret(cfg.secret_name, cfg.namespace)
        log(f"deleted secret {cfg.namespace}/{cfg.secret_name}")
    except ApiException as e:
        if e.status != 404:
            raise


async def run(mode: str, cfg: Config, core, log=print, sleep=asyncio.sleep, clock=time.monotonic) -> int:
    deadline = clock() + cfg.timeout
    attempt = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        while True:
            attempt += 1
            try:
                if mode == "setup":
                    await setup_once(cfg, session, core, log)
                else:
                    await teardown_once(cfg, session, core, log)
                return 0
            except (SetupError, aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                message = str(e) or e.__class__.__name__
                if mode == "teardown":
                    log(f"teardown incomplete, continuing: {message}")
                    return 0
                if clock() >= deadline:
                    log(f"setup failed after {attempt} attempts: {message}")
                    return 1
                log(f"attempt {attempt}: {message}; retrying in {cfg.retry_delay:.0f} s")
                await sleep(cfg.retry_delay)


def main(argv) -> int:
    if len(argv) != 2 or argv[1] not in ("setup", "teardown"):
        print("usage: kc_setup.py setup|teardown")
        return 2
    from kubernetes import client as k8s, config as k8s_config

    k8s_config.load_incluster_config()
    try:
        cfg = Config()
    except SetupError as e:
        print(str(e))
        return 0 if argv[1] == "teardown" else 1
    return asyncio.run(run(argv[1], cfg, k8s.CoreV1Api()))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
