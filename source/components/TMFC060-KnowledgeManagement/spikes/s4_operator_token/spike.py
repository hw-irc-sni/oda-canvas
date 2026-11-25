"""Spike S4 on the kind Canvas: an operator client with view-clients + manage-users grants itself the provider's
canvasSystemRole, calls TMFC060 with a client-credentials token, and grants/revokes knowledge-viewer on a consumer
service account. Secrets are kept in memory and never printed. Test clients are deleted at the end."""
import base64, json, os, ssl, sys, urllib.parse, urllib.request, urllib.error

KC = os.environ["KC_URL"]            # port-forward to canvas-keycloak, .../auth
HOST = os.environ["KC_HOST"]         # in-cluster issuer host
GW = os.environ["GATEWAY"]
REALM = "odari"
PROVIDER = os.environ.get("PROVIDER", "kmk-knowledgemanagement")
OP, CONS = "s4-test-operator", "s4-test-consumer"
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
results = []


def req(method, url, token=None, body=None, form=None, host=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
    if form is not None:
        data = urllib.parse.urlencode(form).encode(); headers["Content-Type"] = "application/x-www-form-urlencoded"
    if host or url.startswith(KC):
        headers["Host"] = host or HOST   # as a pod in the cluster would call Keycloak
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, context=ctx, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw and raw[:1] in b"[{" else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("ok   " if cond else "FAIL ") + name + (f": {detail}" if detail and not cond else ""))


def claims(tok):
    p = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def admin_token():
    s, b = req("POST", f"{KC}/realms/master/protocol/openid-connect/token",
               form={"grant_type": "password", "client_id": "admin-cli",
                     "username": os.environ["KC_ADMIN_USER"], "password": os.environ["KC_ADMIN_PASSWORD"]})
    assert s == 200, s
    return b["access_token"]


def cc_token(client_id, secret):
    s, b = req("POST", f"{KC}/realms/{REALM}/protocol/openid-connect/token", host=HOST,
               form={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret})
    assert s == 200, (s, b)
    return b["access_token"]


A = f"{KC}/admin/realms/{REALM}"


def client(tok, cid):
    s, b = req("GET", f"{A}/clients?clientId={urllib.parse.quote(cid)}", tok)
    return b[0] if s == 200 and b else None


def sa_user(tok, uuid):
    s, b = req("GET", f"{A}/clients/{uuid}/service-account-user", tok)
    return b if s == 200 else None


def role(tok, uuid, name):
    s, b = req("GET", f"{A}/clients/{uuid}/roles/{urllib.parse.quote(name)}", tok)
    return b if s == 200 else None


adm = admin_token()
try:
    for cid in (OP, CONS):
        req("POST", f"{A}/clients", adm, {"clientId": cid, "serviceAccountsEnabled": True, "standardFlowEnabled": False,
                                         "publicClient": False})
    op = client(adm, OP); cons = client(adm, CONS)
    s, sec = req("GET", f"{A}/clients/{op['id']}/client-secret", adm)
    op_secret = sec["value"]
    rm = client(adm, "realm-management")
    op_sa = sa_user(adm, op["id"])
    rm_roles = [role(adm, rm["id"], n) for n in ("view-clients", "manage-users")]
    s, _ = req("POST", f"{A}/users/{op_sa['id']}/role-mappings/clients/{rm['id']}", adm, rm_roles)
    check("admin: operator client created with realm-management view-clients + manage-users", s == 204, s)

    # --- From here on, only the operator's own client-credentials token ---
    t0 = cc_token(OP, op_secret)
    s, b = req("GET", f"{GW}/{PROVIDER}/tmf-api/knowledgeManagement/v5/knowledge", t0)
    check("before the self-grant, TMFC060 refuses the operator token", s in (401, 403), s)

    prov = client(t0, PROVIDER)
    check("operator finds the provider client (view-clients)", prov is not None)
    canvas_role = role(t0, prov["id"], "canvasRole")
    check("operator reads the provider's canvasRole", canvas_role is not None)
    me = sa_user(t0, op["id"])
    check("operator reads its own service-account user", me is not None)
    s, b = req("POST", f"{A}/users/{me['id']}/role-mappings/clients/{prov['id']}", t0, [canvas_role])
    check("operator maps the provider's canvasRole to itself (manage-users)", s == 204, (s, b))

    t1 = cc_token(OP, op_secret)
    c = claims(t1)
    aud = c["aud"] if isinstance(c["aud"], list) else [c["aud"]]
    check("token aud contains the provider client (audience resolve)", PROVIDER in aud, aud)
    check("token carries resource_access[provider].roles canvasRole",
          "canvasRole" in c.get("resource_access", {}).get(PROVIDER, {}).get("roles", []), c.get("resource_access"))
    check("token issuer is the in-cluster Keycloak", c["iss"] == f"http://{HOST}/auth/realms/{REALM}", c["iss"])
    s, b = req("GET", f"{GW}/{PROVIDER}/tmf-api/knowledgeManagement/v5/knowledge", t1)
    check("TMFC060 GET /knowledge with the operator token", s == 200, (s, b))
    s, b = req("GET", f"{GW}/{PROVIDER}/tmf-api/knowledgeManagement/v5/knowledgeSpecification", t1)
    check("TMFC060 GET /knowledgeSpecification with the operator token", s == 200, (s, b))

    # consumer grant and revoke
    cons_sa = sa_user(t0, cons["id"])
    viewer = role(t0, prov["id"], "knowledge-viewer")
    s, b = req("POST", f"{A}/users/{cons_sa['id']}/role-mappings/clients/{prov['id']}", t0, [viewer])
    check("operator grants knowledge-viewer to the consumer service account", s == 204, (s, b))
    s, b = req("GET", f"{A}/users/{cons_sa['id']}/role-mappings/clients/{prov['id']}", t0)
    check("consumer mapping visible", s == 200 and [r["name"] for r in b] == ["knowledge-viewer"], b)
    s, b = req("DELETE", f"{A}/users/{cons_sa['id']}/role-mappings/clients/{prov['id']}", t0, [viewer])
    check("operator revokes knowledge-viewer", s == 204, (s, b))
    s, b = req("GET", f"{A}/users/{cons_sa['id']}/role-mappings/clients/{prov['id']}", t0)
    check("consumer mapping gone", s == 200 and b == [], b)

    # no escalation
    manage_realm = role(adm, rm["id"], "manage-realm")
    s, b = req("POST", f"{A}/users/{me['id']}/role-mappings/clients/{rm['id']}", t0, [manage_realm])
    check("operator cannot give itself realm-management manage-realm", s == 403, s)
    s, b = req("POST", f"{A}/clients", t0, {"clientId": "s4-should-not-exist"})
    check("operator cannot create clients (no manage-clients)", s == 403, s)
finally:
    adm = admin_token()
    for cid in (OP, CONS, "s4-should-not-exist"):
        c = client(adm, cid)
        if c:
            req("DELETE", f"{A}/clients/{c['id']}", adm)
    print("cleanup: test clients deleted:", all(client(adm, cid) is None for cid in (OP, CONS, "s4-should-not-exist")))

bad = [n for n, ok in results if not ok]
print(f"{len(results) - len(bad)}/{len(results)} ok")
sys.exit(1 if bad else 0)
