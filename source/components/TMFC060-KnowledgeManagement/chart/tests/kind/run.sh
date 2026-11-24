#!/usr/bin/env bash
# TMFC060 on a kind Canvas: install the chart, wait for the Component to be deployed, then run scenario.py (smoke checks
# of every ExposedAPI through the gateway, the Keycloak roles, and the PHASE 2 pipeline scenario). Not run in CI.
#
# Prerequisites:
#   - a kind Canvas (canvas-oda) and the three images built and loaded:
#       docker build -t tmforumodacanvas/<image>:0.1.0-ai-knowledge <microservice> && kind load docker-image ...
#   - a model route with a chat and an embedding model, given as extra helm arguments, for example the Canvas
#     agentgateway with an embedding model routed:
#       tests/kind/run.sh --set models.agentgateway.url=http://agentgateway.canvas.svc.cluster.local/v1 \
#         --set models.agentgateway.chatModel=qwen3.5:9b --set models.agentgateway.embeddingModel=bge-m3
#   - kubectl, helm, python3; the Keycloak admin credentials are read from the canvas Helm release and never printed
# Environment: RELEASE (default kmk), NAMESPACE (components), EXPECT_ROUTE (agentgateway), KEEP=1 keeps the release,
#   GATEWAY (default: a port-forward to the Istio ingress on GW_PORT, 18443).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CHART="$(cd "$HERE/../.." && pwd)"
RELEASE="${RELEASE:-kmk}"
NAMESPACE="${NAMESPACE:-components}"
KC_PORT="${KC_PORT:-18083}"

cleanup() {
  [ -n "${PF:-}" ] && kill "$PF" 2>/dev/null || true
  [ -n "${GW_PF:-}" ] && kill "$GW_PF" 2>/dev/null || true
  if [ "${KEEP:-}" != 1 ]; then
    helm uninstall "$RELEASE" -n "$NAMESPACE" --wait >/dev/null 2>&1 || true
    # the release keeps its data on uninstall (see the chart README): this run's data goes too
    kubectl delete pvc -n "$NAMESPACE" "data-$RELEASE-kmpg-0" "store-$RELEASE-kmapi-0" \
      "qdrant-storage-$RELEASE-qdrant-0" --ignore-not-found >/dev/null
    kubectl delete secret -n "$NAMESPACE" "$RELEASE-kmpg-credentials" --ignore-not-found >/dev/null
  fi
}
trap cleanup EXIT

if ! ls "$CHART"/charts/qdrant-*.tgz >/dev/null 2>&1; then
  helm repo add qdrant https://qdrant.github.io/qdrant-helm >/dev/null 2>&1 || true
  helm dependency build "$CHART" >/dev/null
fi
echo "installing $RELEASE into $NAMESPACE"
helm install "$RELEASE" "$CHART" -n "$NAMESPACE" "$@" >/dev/null
kubectl wait -n "$NAMESPACE" --for=condition=Ready pod -l "app.kubernetes.io/instance=$RELEASE" --timeout=600s >/dev/null
for _ in $(seq 1 60); do
  state=$(kubectl get components -n "$NAMESPACE" "$RELEASE-knowledgemanagement" \
    -o jsonpath='{.status.summary/status.deployment_status}' 2>/dev/null || true)
  [ "$state" = Complete ] && break
  sleep 5
done
echo "Component $RELEASE-knowledgemanagement: ${state:-unknown}"

kubectl port-forward -n canvas svc/canvas-keycloak "$KC_PORT:8083" >/dev/null 2>&1 &
PF=$!
sleep 3
read -r KC_ADMIN_USER KC_ADMIN_PASSWORD < <(helm get values canvas -n canvas -a -o json | python3 -c \
  "import json,sys; a=json.load(sys.stdin)['keycloak']['auth']; print(a.get('adminUser','admin'), a['adminPassword'])")
export KC_ADMIN_USER KC_ADMIN_PASSWORD RELEASE NAMESPACE
export KC_URL="http://localhost:$KC_PORT/auth"
# Tokens must name the in-cluster Keycloak as issuer (the chart's default KEYCLOAK_ISSUERS)
export KC_HOST="canvas-keycloak-headless.canvas.svc.cluster.local:8083"
export FIXTURES="$CHART/../pipelineWorkerMicroservice/tests/fixtures"
# The Canvas gateway: GATEWAY if given, else a port-forward to the Istio ingress (the host's ports 80 and 443 may
# belong to something else)
if [ -z "${GATEWAY:-}" ]; then
  kubectl port-forward -n istio-ingress svc/istio-ingress "${GW_PORT:-18443}:443" >/dev/null 2>&1 &
  GW_PF=$!
  sleep 3
  GATEWAY="https://127.0.0.1:${GW_PORT:-18443}"
fi
export GATEWAY
python3 "$HERE/scenario.py"
