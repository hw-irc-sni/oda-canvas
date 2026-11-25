#!/usr/bin/env bash
# DependentAPI CRD checks (apiType mcp and dependencyKind, ADR-010 data resource bindings): applies the CRD rendered
# from charts/oda-crds and server-side dry-runs the samples (see ../lib.sh). Environment as dataresourceconfig/run.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../lib.sh
. "$HERE/../lib.sh"

apply_crd oda-dependentapi-crd.yaml dependentapis.oda.tmforum.org
run_samples "$HERE/samples"
finish
