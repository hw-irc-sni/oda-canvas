#!/usr/bin/env bash
# Spike S2 (hw-irc-sni/oda-canvas#71): can the Canvas Portal backend pod call the ExposedAPI URL that TMF639
# Resource Inventory reports? Read-only: it picks an ExposedAPI, then calls the reported URL, the same path through
# the in-cluster ingress Service, and the in-cluster implementation Service URL, from inside the portal backend pod.
# Usage: ./run.sh [namespace/exposedapi [resource]]   (default: the first ready openapi ExposedAPI in namespace
# components; resource is appended to the API path, for example /catalog)
# Needs: kubectl, python3 (locally), and the portal backend pod (its image has python3 and httpx).
set -euo pipefail
PORTAL_NS=${PORTAL_NS:-canvas-portal}
PORTAL_DEPLOY=${PORTAL_DEPLOY:-canvas-portal-backend}
INGRESS=${INGRESS:-istio-ingress.istio-ingress.svc.cluster.local}

SUFFIX=${2:-}
if [ $# -gt 0 ]; then
  NS=${1%%/*}; NAME=${1#*/}
else
  NS=components
  NAME=$(kubectl get exposedapis -n "$NS" -o json | python3 -c "
import json,sys
items=[i for i in json.load(sys.stdin)['items'] if i['spec'].get('apiType')=='openapi'
       and (i.get('status') or {}).get('implementation',{}).get('ready')]
print(items[0]['metadata']['name'] if items else '')")
  [ -n "$NAME" ] || { echo "no ready openapi ExposedAPI in namespace $NS" >&2; exit 1; }
fi

read -r URL IMPL PORT APIPATH < <(kubectl get exposedapi "$NAME" -n "$NS" \
  -o jsonpath='{.status.apiStatus.url} {.spec.implementation} {.spec.port} {.spec.path}{"\n"}')
echo "ExposedAPI $NS/$NAME"
echo "  reported url : $URL"
echo "  implementation: $IMPL  port: $PORT  path: $APIPATH"

echo "Namespace labels (sidecar injection):"
kubectl get ns "$PORTAL_NS" "$NS" -o jsonpath='{range .items[*]}  {.metadata.name}: {.metadata.labels}{"\n"}{end}'
echo "Mesh and gateway auth policies:"
kubectl get requestauthentication,authorizationpolicy,peerauthentication -A --no-headers 2>/dev/null | sed 's/^/  /' || true
[ -n "$(kubectl get requestauthentication,authorizationpolicy,peerauthentication -A --no-headers 2>/dev/null)" ] || echo "  none"

echo "Calls from $PORTAL_NS/$PORTAL_DEPLOY:"
kubectl exec -i -n "$PORTAL_NS" "deploy/$PORTAL_DEPLOY" -- python3 - "$URL" "$INGRESS" "$IMPL" "$NS" "$PORT" "$APIPATH" "$SUFFIX" <<'PY'
import sys
from urllib.parse import urlsplit
import httpx
url, ingress, impl, ns, port, path, suffix = sys.argv[1:]
url, path = url + suffix, path + suffix
gateway_path = urlsplit(url).path
probes = [
    ("reported url", url, (True, False)),
    ("ingress Service, http", f"http://{ingress}{gateway_path}", (True,)),
    ("ingress Service, https", f"https://{ingress}{gateway_path}", (True, False)),
    ("implementation Service", f"http://{impl}.{ns}.svc.cluster.local:{port}{path}", (True,)),
]
for name, target, verifies in probes:
    for verify in verifies:
        label = f"{name}" + ("" if target.startswith("http:") else f", verify={verify}")
        try:
            r = httpx.get(target, timeout=5, verify=verify)
            print(f"  {label:<40} HTTP {r.status_code}  {target}")
        except httpx.HTTPError as exc:
            print(f"  {label:<40} {type(exc).__name__}: {str(exc)[:70]}  {target}")
PY
