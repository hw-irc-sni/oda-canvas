# agentgateway MCP Operator (TMFOP013)

Registers the MCP ExposedAPIs of ODA Components with agentgateway (hw-irc-sni/oda-canvas#68). Chart:
[`charts/agentgateway-mcp-operator`](../../../../charts/agentgateway-mcp-operator).

## What it does

- Every ExposedAPI with `apiType: mcp` whose implementation is ready (in `WATCHED_NAMESPACES`) becomes an entry of
  `mcp.targets` in the agentgateway config (ConfigMap `agentgateway-config`, key `config.yaml`):

  ```yaml
  - name: oda-<namespace>-<exposedapi>          # for TMFC060: oda-components-<release>-knowledgemanagement-knowledgemcp
    mcp:
      host: http://<spec.implementation>.<namespace>.svc.cluster.local:<spec.port><spec.path>
    policies:
      backendAuth:
        passthrough: {}                         # the caller's own token is forwarded
  ```

  Through the gateway's MCP endpoint its tools are listed as `<target>_<tool>`.
- When the ExposedAPI is deleted (the component is uninstalled) or stops being ready, its target is removed.
- **Only targets named `oda-…` are the operator's.** Targets added by hand or in the Canvas Portal are never changed
  and keep their place; the portal's MCP editor in turn treats `oda-…` targets as read-only (#69).
- If the config has no `mcp` section yet, the operator adds one (`gateways: GATEWAY_MCP_GATEWAY`,
  `statefulMode: stateless`, `failureMode: failOpen`).

**Authentication.** agentgateway forwards the caller's bearer token to the component (backendAuth passthrough), so an
agent calls each component with its own credentials and roles (Pattern A, ADR-010). TMFC060's MCP API needs a token on
every request: with the gateway's `mcp.failureMode: failOpen` (the agentgateway chart's kind overlay), a client that
sends none still gets the other targets' tools.

**How it reconciles.** It watches ExposedAPIs with a kopf event handler only, so it writes nothing to them (no
annotations, no finalizer). Every event, and a periodic resync (`RESYNC_INTERVAL_SECONDS`), reconciles all targets
at once: read the ConfigMap, merge, write only on a change, with the resourceVersion read (a concurrent change by
the portal gives a 409 and the merge is redone). After a write it nudges the gateway pods (an annotation), so the
mounted config is refreshed now instead of within a minute. The resync also restores the targets after a
`helm upgrade` of agentgateway resets the ConfigMap.

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `GATEWAY_NAMESPACE` | `canvas` | Namespace of the agentgateway release |
| `GATEWAY_CONFIG_MAP`, `GATEWAY_CONFIG_KEY` | `agentgateway-config`, `config.yaml` | The gateway config |
| `GATEWAY_POD_SELECTOR` | `app.kubernetes.io/name=agentgateway-standalone` | Gateway pods to nudge |
| `GATEWAY_MCP_GATEWAY` | `default` | Gateway of an mcp section the operator creates |
| `WATCHED_NAMESPACES` | all (the chart: `components,odacompns-*`) | Namespaces whose ExposedAPIs are registered (globs) |
| `RESYNC_INTERVAL_SECONDS` | `60` | Full reconciliation interval |
| `LOGGING` | `20` | Python log level |

## Layout, build, test

```
docker/src/agentgatewayMcpOperator.py   kopf handlers and the reconcile loop
docker/src/gateway.py                    the ConfigMap read/write (409 retry), pod nudge, reconcile
docker/src/targets.py                    target names and entries, the merge into mcp.targets
docker/tests/                            pytest, with fake Kubernetes clients
```

```bash
cd docker && pip install -r requirements.txt -r requirementsTest.txt && python -m pytest -q tests
docker build -t tmforumodacanvas/agentgateway-mcp-operator:0.1.0-ai-knowledge .
```
