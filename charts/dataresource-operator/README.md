# dataresource-operator

Helm chart of the TMFOP012 Data Resource Management operator
([source](../../source/operators/TMFOP012-Data-Resource-Management/dataresource-operator)). Installed by `canvas-oda`
when `dataresource-operator.enabled` is true (off by default), or on its own in the Canvas namespace.

## What it installs

- The operator Deployment (Canvas Portal labels `oda.tmforum.org/operator*`, operator id TMFOP012), non-root with a
  read-only root filesystem, and its ServiceAccount with a ClusterRole:
  - `dataresourceconfigs` (get, list, watch, patch) and their `status` (get, patch);
  - `components`, `exposedapis` (read); `dependentapis` (read, create, patch, delete);
  - `secrets` (create, patch, delete only: binding Secrets are written without reading any Secret);
  - CRDs and namespaces (list, watch) and events (create) for kopf.
- With `events.enabled` (default): the Service of the hub event listener (`events.listenerPort`), registered on
  provider hubs as `events.callbackBase` (default: this Service's in-cluster URL).
- With `keycloak.setup.enabled` (default):
  - a Job per release revision that creates the operator's Keycloak client `oidc.clientId` (realm-management
    `view-clients`, `manage-users`) and the Secret `oidc.secretName`, using the admin password from
    `keycloak.adminSecret`. It is not a Helm hook, so an install doesn't wait for Keycloak; it retries for up to
    `keycloak.setup.timeoutSeconds`;
  - a pre-delete hook Job that deletes the client and the Secret;
  - their ServiceAccount and a Role limited to that Secret.

## Values

| Value | Default | Meaning |
|---|---|---|
| `image`, `version`, `prereleaseSuffix` | `tmforumodacanvas/dataresource-operator`, `0.2.0`, empty | Image `<image>:<version>[-<prereleaseSuffix>]`, with `global.image.registry` |
| `imagePullPolicy` / `prereleasePullPolicy` | `IfNotPresent` / `Always` | Pull policy of a release / prerelease image |
| `loglevel` | `'20'` | Python log level |
| `pollIntervalSeconds` | `60` | Provider poll interval |
| `componentNamespace` | `components` | Default provider namespace |
| `events.enabled`, `listenerPort`, `callbackBase` | `true`, `8081`, empty (the Service URL) | Provider hub events; disabled: polling only |
| `keycloak.url` | `http://canvas-keycloak-headless.canvas.svc.cluster.local:8083/auth` | Must match the issuer host providers expect |
| `keycloak.realm` | `odari` | |
| `keycloak.adminUser`, `adminSecret`, `adminPasswordKey` | `admin`, `canvas-keycloak`, `admin-password` | Admin credentials for the setup Jobs only |
| `keycloak.setup.enabled`, `timeoutSeconds`, `ttlSecondsAfterFinished` | `true`, `900`, `3600` | Setup Job |
| `oidc.clientId`, `oidc.secretName` | `dataresource-operator`, `dataresource-operator-oidc-secret` | Operator client and its credentials Secret |
| `resources` | 50m / 96Mi requests, 256Mi limit | |

## Install on its own (kind, locally built image)

```bash
helm install dro charts/dataresource-operator -n canvas \
  --set prereleaseSuffix=ai-knowledge --set prereleasePullPolicy=IfNotPresent
```

Uninstall after the DataResourceConfigs it handles are gone (they carry its finalizer).
