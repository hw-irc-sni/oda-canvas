#!/usr/bin/env bash
# Spike S1 (hw-irc-sni/oda-canvas#70): does a Canvas Portal token carry the TMFC060 client roles?
# Runs against a local kind Canvas (realm odari, Keycloak service canvas/canvas-keycloak). It installs the
# knowledgemanagement-v1 test chart as release s1km, creates a temporary user with knowledge-viewer, gets a
# token through the canvas-portal client (password grant), prints the relevant claims, and cleans up.
# Needs: kubectl, helm, curl, python3; the Keycloak admin credentials from the canvas Helm release.
set -euo pipefail
cd "$(dirname "$0")/../../../../../feature-definition-and-test-kit/testData"
KC=http://localhost:18083/auth
kubectl port-forward -n canvas svc/canvas-keycloak 18083:8083 >/dev/null 2>&1 &
PF=$!; trap 'kill $PF 2>/dev/null; helm uninstall s1km -n components >/dev/null 2>&1 || true' EXIT
sleep 3
read -r KCU KCP < <(helm get values canvas -n canvas -a -o json | python3 -c \
  "import json,sys; a=json.load(sys.stdin)['keycloak']['auth']; print(a.get('adminUser','admin'), a['adminPassword'])")
admin_token() { curl -s -d grant_type=password -d client_id=admin-cli --data-urlencode "username=$KCU" \
  --data-urlencode "password=$KCP" $KC/realms/master/protocol/openid-connect/token | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])"; }
json() { python3 -c "import json,sys;d=json.load(sys.stdin);print($1)"; }

echo "canvas-portal client:"
curl -s -H "Authorization: Bearer $(admin_token)" "$KC/admin/realms/odari/clients?clientId=canvas-portal" \
  | json "{k:d[0].get(k) for k in ['publicClient','fullScopeAllowed','directAccessGrantsEnabled','defaultClientScopes']}"

helm install s1km knowledgemanagement-v1 -n components --wait --timeout 8m >/dev/null
until [ "$(kubectl get component s1km-knowledgemanagement -n components -o jsonpath='{.status.summary/status.deployment_status}')" = Complete ]; do sleep 10; done

H="Authorization: Bearer $(admin_token)"
PW=$(python3 -c "import secrets;print(secrets.token_urlsafe(18))")
curl -s -H "$H" -H 'Content-Type: application/json' $KC/admin/realms/odari/users -d "{\"username\":\"s1-viewer\",\"enabled\":true,\"emailVerified\":true,\"firstName\":\"S1\",\"lastName\":\"Viewer\",\"email\":\"s1-viewer@example.org\",\"credentials\":[{\"type\":\"password\",\"value\":\"$PW\",\"temporary\":false}]}"
USER_ID=$(curl -s -H "$H" "$KC/admin/realms/odari/users?username=s1-viewer&exact=true" | json "d[0]['id']")
trap 'curl -s -X DELETE -H "Authorization: Bearer $(admin_token)" $KC/admin/realms/odari/users/$USER_ID; kill $PF 2>/dev/null; helm uninstall s1km -n components >/dev/null 2>&1 || true' EXIT
CLIENT_ID=$(curl -s -H "$H" "$KC/admin/realms/odari/clients?clientId=s1km-knowledgemanagement" | json "d[0]['id']")
ROLE=$(curl -s -H "$H" "$KC/admin/realms/odari/clients/$CLIENT_ID/roles/knowledge-viewer")
curl -s -H "$H" -H 'Content-Type: application/json' -d "[$ROLE]" $KC/admin/realms/odari/users/$USER_ID/role-mappings/clients/$CLIENT_ID

echo "portal token claims:"
curl -s -d grant_type=password -d client_id=canvas-portal --data-urlencode "username=s1-viewer" --data-urlencode "password=$PW" \
  $KC/realms/odari/protocol/openid-connect/token | python3 -c "
import json,sys,base64
p=json.load(sys.stdin)['access_token'].split('.')[1]; p+='='*(-len(p)%4); c=json.loads(base64.urlsafe_b64decode(p))
print(json.dumps({k:c.get(k) for k in ['iss','aud','azp','scope','resource_access','realm_access']},indent=1))"
