# Data Resource Operator (TMFOP012)

A [kopf](https://kopf.readthedocs.io) operator for `DataResourceConfig` resources (`oda.tmforum.org/v1alpha1`) with
`spec.provider: odaComponent` (ADR-010 §7). Chart: [`charts/dataresource-operator`](../../../../charts/dataresource-operator).

> **Status: scaffold (hw-irc-sni/oda-canvas#62).** The handlers log each resource and set `status.phase: Pending`
> with a `Ready` condition `Unknown` (reason `NotImplemented`). The provider role comes with #63, the consumer role
> with #64.

## What it handles

| DataResourceConfig | Handling |
|---|---|
| `provider: odaComponent`, `role: provider` | Mirrors the knowledge-bases and ontology modules of the provider component into `status.providedResources[]`; polls every `POLL_INTERVAL_SECONDS` and listens on the provider's event hub (#63) |
| `provider: odaComponent`, `role: consumer` (default) | Ensures the knowledge-base on the provider, grants the consumer component the provider's client roles, writes a binding Secret and a DependentAPI owned by the CR; on delete, revokes the roles and keeps the knowledge-base (#64) |
| any other provider (for example `databricks`) | Never touched: no status, finalizer or annotation (ADR-010 §8) |

Every handler is filtered on `spec.provider`. kopf keeps its state in annotations prefixed
`dataresource.oda.tmforum.org/` and uses the finalizer `dataresource.oda.tmforum.org/finalizer`, so it doesn't collide
with another kopf operator watching DataResourceConfigs. It runs `--standalone` (no peering).

The finalizer means that an owned DataResourceConfig can only be deleted while the operator runs: delete the
DataResourceConfigs (uninstall their components) before uninstalling the operator, or remove the finalizer by hand.

Sequence diagrams: [provider](sequenceDiagrams/provider.puml), [consumer](sequenceDiagrams/consumer.puml).

## Keycloak

Decided in spike S4 (hw-irc-sni/oda-canvas#61, `source/components/TMFC060-KnowledgeManagement/spikes/s4_operator_token`):

- The chart's setup Job (`kc_setup.py setup`, one per release revision) uses the Keycloak admin password from the
  `canvas-keycloak` Secret. It creates the confidential client `dataresource-operator` (service account only) with
  realm-management `view-clients` and `manage-users`, and writes the Secret `dataresource-operator-oidc-secret`
  (`client_id`, `client_secret`, `token_url`). It retries until Keycloak and the realm are up. A pre-delete hook
  (`kc_setup.py teardown`) removes the client and the Secret.
- The operator holds only those client credentials, mounted at `OIDC_DIR` and read on every token fetch.
  - To call a provider, it maps the provider's `canvasSystemRole` to its own service account; its token then carries
    the role and the provider client in `aud`, which TMFC060 requires.
  - To grant a consumer access, it maps the provider's client roles to `service-account-<consumer component>`.
- Keycloak checks a token's issuer against the request host: `KEYCLOAK_URL` must be the URL the providers expect as
  issuer (default: the in-cluster headless Service).

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `KEYCLOAK_URL` | `http://canvas-keycloak-headless.canvas.svc.cluster.local:8083/auth` | Keycloak base URL |
| `KEYCLOAK_REALM` | `odari` | Canvas realm |
| `OIDC_DIR` | `/etc/dataresource-operator/oidc` | Mounted client credentials |
| `POLL_INTERVAL_SECONDS` | `60` | Provider poll interval |
| `COMPONENT_NAMESPACE` | `components` | Default provider namespace |
| `COMPONENTNAME_LABEL` | `oda.tmforum.org/componentName` | Label linking a resource to its component |
| `LOGGING` | `20` | Python log level |

## Layout

```
docker/
  Dockerfile, DockerfileTest, requirements*.txt
  src/dataResourceOperator.py   kopf handlers
  src/keycloak.py               client-credentials tokens and the admin calls (find clients, map/unmap roles)
  src/kc_setup.py               setup/teardown of the operator's Keycloak client (chart Jobs)
  src/settings.py, log_wrapper.py
  tests/                        pytest, with an in-memory Keycloak (fake_keycloak.py)
sequenceDiagrams/
```

## Build, test, run

```bash
cd docker
pip install -r requirements.txt -r requirementsTest.txt && python -m pytest -q tests

# kind: build, load, install (off by default in canvas-oda; here as its own release)
docker build -t tmforumodacanvas/dataresource-operator:0.1.0-ai-knowledge .
kind load docker-image tmforumodacanvas/dataresource-operator:0.1.0-ai-knowledge --name oda-canvas
helm install dro ../../../../../charts/dataresource-operator -n canvas \
  --set prereleaseSuffix=ai-knowledge --set prereleasePullPolicy=IfNotPresent
```

CI: `.github/workflows/tmfop012-unit-tests.yml` (pytest and chart lint); the image workflows
`dockerbuild-{prerelease,release}-dataresource-operator.yml` are generated from
`automation/generators/dockerbuild-workflow-generator/dockerbuild-config.yaml`.
