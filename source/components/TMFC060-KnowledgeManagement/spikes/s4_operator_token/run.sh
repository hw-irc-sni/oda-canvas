#!/usr/bin/env bash
# Spike S4 on a kind Canvas with a TMFC060 release installed (PROVIDER, default kmk-knowledgemanagement).
# Reads the Keycloak admin credentials from the canvas Helm release (never printed); creates two test clients and
# deletes them at the end.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
kubectl port-forward -n canvas svc/canvas-keycloak 18083:8083 >/dev/null 2>&1 & PF=$!
kubectl port-forward -n istio-ingress svc/istio-ingress 18443:443 >/dev/null 2>&1 & GPF=$!
trap 'kill $PF $GPF 2>/dev/null || true' EXIT
sleep 3
read -r KC_ADMIN_USER KC_ADMIN_PASSWORD < <(helm get values canvas -n canvas -a -o json | python3 -c \
  "import json,sys; a=json.load(sys.stdin)['keycloak']['auth']; print(a.get('adminUser','admin'), a['adminPassword'])")
export KC_ADMIN_USER KC_ADMIN_PASSWORD
export KC_URL=http://localhost:18083/auth KC_HOST=canvas-keycloak-headless.canvas.svc.cluster.local:8083
export GATEWAY=https://127.0.0.1:18443
python3 "$HERE/spike.py"
