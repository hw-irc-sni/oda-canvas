#!/usr/bin/env bash
# DataResourceConfig CRD checks against a live API server (the CEL rules need one): applies the CRD rendered from
# charts/oda-crds, then server-side dry-runs every sample in samples/ (see ../lib.sh for the expectation format).
# Run in CI on a throwaway kind cluster, or on a kind Canvas. Environment: NAMESPACE (default "default"),
# APPLY_CRD=0 to use the CRD already installed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
. "$HERE/../lib.sh"
CRD=dataresourceconfigs.oda.tmforum.org

apply_crd oda-dataresourceconfig-crd.yaml "$CRD"
run_samples "$HERE/samples"

# Defaults: role consumer, access read, required true
got="$(kubectl apply --dry-run=server -n "$NAMESPACE" -f "$HERE/samples/valid-03-adr010-consumer.yaml" -o \
  jsonpath='{.spec.role} {.spec.dependentDataResources[0].access} {.spec.dependentDataResources[0].required}')"
if [ "$got" = "consumer read true" ]; then ok "defaults role, access, required: $got"; else fail "defaults: $got"; fi

# Printer columns
cols="$(kubectl get crd "$CRD" -o jsonpath='{.spec.versions[0].additionalPrinterColumns[*].name}')"
if [ "$cols" = "Provider Role Phase Resources Age" ]; then ok "printer columns: $cols"; else fail "printer columns: $cols"; fi
finish
