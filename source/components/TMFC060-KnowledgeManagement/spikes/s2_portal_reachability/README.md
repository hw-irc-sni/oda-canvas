# Spike S2: can the Canvas Portal backend call the ExposedAPI URL from Resource Inventory?

Issue: [hw-irc-sni/oda-canvas#71](https://github.com/hw-irc-sni/oda-canvas/issues/71). Run on 2026-11-24 on the local kind
Canvas (canvas-oda 1.2.6-rc8 with api-operator-istio, canvas-portal 0.1.3). It is read-only: nothing is installed or changed.
Re-run with `./run.sh [namespace/exposedapi [resource]]`, for example
`./run.sh components/pc-productcatalogmanagement-productcatalogmanagement-v4 /catalog`.

## Question

TMF639 Resource Inventory reports each ExposedAPI as an `API` resource whose `url` characteristic is the ExposedAPI's
`status.apiStatus.url`, the address the gateway publishes. Until now the portal only displayed these URLs. Can the
portal backend pod call them, so that instance discovery (#72) can use them for the TMFC060 proxy?

Resource Inventory 1.2.0 was disabled on this Canvas, so the probe used the ExposedAPIs of the deployed
`pc` (ProductCatalog) component. They are published by the same api-operator-istio mechanism as TMFC060's.

## Answer: no, not on kind

Results from inside `canvas-portal/canvas-portal-backend`, with the image's own Python and httpx:

| Target | Result |
|---|---|
| Reported `url`, `https://127.0.0.1/pc-productcatalogmanagement/tmf-api/productCatalogManagement/v4/catalog` | **Connection refused.** The host is the ingress LoadBalancer's external address, `127.0.0.1` on kind, which in a pod is the pod itself |
| Same path through the in-cluster ingress Service, `http://istio-ingress.istio-ingress.svc.cluster.local/…` | HTTP 301, a redirect to https |
| Same, `https://istio-ingress.istio-ingress.svc.cluster.local/…` | Certificate verification fails (self-signed); **HTTP 200 only with verification turned off** |
| Implementation Service, `http://pc-prodcatapi.components.svc.cluster.local:8080/pc-productcatalogmanagement/…/catalog` | **HTTP 200** |

Other findings:
- **No mesh or gateway auth policies** exist (no RequestAuthentication, AuthorizationPolicy or PeerAuthentication).
  `components` has sidecar injection and `canvas-portal` doesn't, so mTLS is permissive and plain http from the portal
  reaches the Service. A Canvas with STRICT mTLS for `components` would need the portal in the mesh too.
- **TMFC060 doesn't depend on the gateway for security.** It validates the forwarded user JWT and its roles itself
  (spike S1, #70). Calling the Service directly bypasses only gateway-level policies, such as rate limits or Kong/APISIX
  API keys.
- **The TMFC060 Service serves the full ExposedAPI `path`.** The gateway routes by prefix without rewriting
  (`chart/templates/_helpers.tpl`), so `http://<implementation>.<namespace>.svc.cluster.local:<port><path>` is the
  same API as the gateway URL.
- Resource Inventory 1.2.0 gives an `API` resource `url` and `implementation`, but **not `port` or `path`**.

## URL strategy for instance discovery (#72, chosen by the user)

- **Default, `KNOWLEDGE_URL_MODE=service`:** the portal builds the in-cluster Service URL
  `http://<implementation>.<namespace>.svc.cluster.local:<port><path>`. **Resource Inventory gains `port` and `path`
  characteristics on `API` resources**, so discovery reads everything from Resource Inventory.
- **`KNOWLEDGE_URL_MODE=gateway`:** the portal calls the reported `url` unchanged. This suits Canvases where the
  public gateway address resolves and verifies from inside the cluster.
- **The static override** (`KNOWLEDGE_INSTANCES_JSON`) covers anything else.
