"""A small in-memory Keycloak for the tests: the token endpoints and the admin calls TMFOP012 makes.

Admin calls need a bearer token it issued. Rights are modelled coarsely: the admin token can do everything; a
client-credentials token can read clients, roles and service-account users with realm-management view-clients, and
map client roles with manage-users, but never realm-management roles it doesn't hold.
"""

import itertools
import uuid

from aiohttp import web
from aiohttp.test_utils import TestServer

REALM = "odari"


class FakeKeycloak:
    def __init__(self, admin_user="admin", admin_password="admin-pw", expires_in=300):
        self.admin_user, self.admin_password = admin_user, admin_password
        self.expires_in = expires_in
        self.clients = {}       # uuid -> representation (with "secret")
        self.roles = {}         # client uuid -> {name: role}
        self.users = {}         # user id -> {"username", "mappings": {client uuid: set(role names)}}
        self.tokens = {}        # token -> {"kind": "admin"|"client", "client_id": ...}
        self.token_requests = 0
        self.down = False
        self._n = itertools.count(1)
        rm = self.add_client("realm-management", service_account=False)
        for name in ("view-clients", "manage-users", "manage-clients", "manage-realm"):
            self.add_role(rm, name)

    # --- state helpers ---
    def add_client(self, client_id, service_account=True, secret=None):
        cid = str(uuid.uuid4())
        self.clients[cid] = {"id": cid, "clientId": client_id, "serviceAccountsEnabled": service_account,
                             "secret": secret or f"secret-{next(self._n)}"}
        self.roles[cid] = {}
        if service_account:
            uid = str(uuid.uuid4())
            self.users[uid] = {"id": uid, "username": f"service-account-{client_id}", "client": cid, "mappings": {}}
        return cid

    def add_role(self, client_uuid, name):
        self.roles[client_uuid][name] = {"id": str(uuid.uuid4()), "name": name, "clientRole": True,
                                         "containerId": client_uuid}

    def by_client_id(self, client_id):
        return next((c for c in self.clients.values() if c["clientId"] == client_id), None)

    def sa_of(self, client_uuid):
        return next((u for u in self.users.values() if u.get("client") == client_uuid), None)

    def mapped(self, client_id, on_client_id):
        sa = self.sa_of(self.by_client_id(client_id)["id"])
        return sorted(sa["mappings"].get(self.by_client_id(on_client_id)["id"], set()))

    # --- auth ---
    def _caller(self, request):
        auth = request.headers.get("Authorization", "")
        info = self.tokens.get(auth.removeprefix("Bearer "))
        if info is None:
            raise web.HTTPUnauthorized()
        return info

    def _has(self, info, role):
        if info["kind"] == "admin":
            return True
        c = self.by_client_id(info["client_id"])
        rm = self.by_client_id("realm-management")
        sa = self.sa_of(c["id"]) if c else None
        return bool(sa) and role in sa["mappings"].get(rm["id"], set())

    def _require(self, request, role):
        info = self._caller(request)
        if not self._has(info, role):
            raise web.HTTPForbidden()
        return info

    # --- endpoints ---
    async def token(self, request):
        if self.down:
            raise web.HTTPServiceUnavailable()
        self.token_requests += 1
        form = await request.post()
        realm = request.match_info["realm"]
        if realm == "master" and form.get("grant_type") == "password":
            if (form.get("username"), form.get("password")) != (self.admin_user, self.admin_password):
                raise web.HTTPUnauthorized()
            kind, client_id = "admin", "admin-cli"
        elif realm == REALM and form.get("grant_type") == "client_credentials":
            c = self.by_client_id(form.get("client_id"))
            if c is None or c["secret"] != form.get("client_secret"):
                raise web.HTTPUnauthorized()
            kind, client_id = "client", c["clientId"]
        else:
            raise web.HTTPBadRequest()
        tok = f"tok-{next(self._n)}"
        self.tokens[tok] = {"kind": kind, "client_id": client_id}
        return web.json_response({"access_token": tok, "expires_in": self.expires_in})

    async def clients_get(self, request):
        self._require(request, "view-clients")
        cid = request.query.get("clientId")
        found = [c for c in self.clients.values() if cid is None or c["clientId"] == cid]
        return web.json_response([{k: v for k, v in c.items() if k != "secret"} for c in found])

    async def clients_post(self, request):
        self._require(request, "manage-clients")
        body = await request.json()
        if self.by_client_id(body["clientId"]):
            raise web.HTTPConflict()
        self.add_client(body["clientId"], body.get("serviceAccountsEnabled", False))
        return web.Response(status=201)

    async def client_delete(self, request):
        self._require(request, "manage-clients")
        cid = request.match_info["cid"]
        if self.clients.pop(cid, None) is None:
            raise web.HTTPNotFound()
        for uid in [u for u, v in self.users.items() if v.get("client") == cid]:
            del self.users[uid]
        return web.Response(status=204)

    async def client_secret(self, request):
        self._require(request, "manage-clients")
        c = self.clients.get(request.match_info["cid"])
        if c is None:
            raise web.HTTPNotFound()
        return web.json_response({"type": "secret", "value": c["secret"]})

    async def sa_user(self, request):
        self._require(request, "view-clients")
        sa = self.sa_of(request.match_info["cid"])
        if sa is None:
            raise web.HTTPNotFound()
        return web.json_response({"id": sa["id"], "username": sa["username"]})

    async def role_get(self, request):
        self._require(request, "view-clients")
        role = self.roles.get(request.match_info["cid"], {}).get(request.match_info["role"])
        if role is None:
            raise web.HTTPNotFound()
        return web.json_response(role)

    async def mappings(self, request):
        user = self.users.get(request.match_info["uid"])
        cid = request.match_info["cid"]
        if user is None or cid not in self.clients:
            raise web.HTTPNotFound()
        if request.method == "GET":
            self._require(request, "view-clients")
            names = user["mappings"].get(cid, set())
            return web.json_response([self.roles[cid][n] for n in sorted(names)])
        info = self._require(request, "manage-users")
        roles = await request.json()
        rm = self.by_client_id("realm-management")
        if cid == rm["id"] and info["kind"] != "admin":
            for r in roles:
                if not self._has(info, r["name"]):
                    raise web.HTTPForbidden()     # Keycloak never lets a caller grant admin roles it lacks
        names = user["mappings"].setdefault(cid, set())
        for r in roles:
            if request.method == "POST":
                names.add(r["name"])
            else:
                names.discard(r["name"])
        return web.Response(status=204)

    def app(self):
        app = web.Application()
        a = f"/auth/admin/realms/{REALM}"
        app.router.add_post("/auth/realms/{realm}/protocol/openid-connect/token", self.token)
        app.router.add_get(f"{a}/clients", self.clients_get)
        app.router.add_post(f"{a}/clients", self.clients_post)
        app.router.add_delete(a + "/clients/{cid}", self.client_delete)
        app.router.add_get(a + "/clients/{cid}/client-secret", self.client_secret)
        app.router.add_get(a + "/clients/{cid}/service-account-user", self.sa_user)
        app.router.add_get(a + "/clients/{cid}/roles/{role}", self.role_get)
        app.router.add_route("*", a + "/users/{uid}/role-mappings/clients/{cid}", self.mappings)
        return app


class Running:
    """``async with Running(fake) as base_url:`` serves the fake Keycloak; base_url ends in /auth."""

    def __init__(self, fake: FakeKeycloak):
        self.server = TestServer(fake.app())

    async def __aenter__(self):
        await self.server.start_server()
        return str(self.server.make_url("/auth"))

    async def __aexit__(self, *exc):
        await self.server.close()
