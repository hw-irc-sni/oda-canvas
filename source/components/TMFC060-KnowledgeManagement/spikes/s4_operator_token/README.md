# Spike S4: how a Canvas operator gets a canvasSystemRole token, and rights to grant client roles

Issue: [hw-irc-sni/oda-canvas#61](https://github.com/hw-irc-sni/oda-canvas/issues/61). Checked on 2026-11-24 on
`ai-knowledge` and on the local kind Canvas (Keycloak 20.0.5, realm `odari`, TMFC060 release `kmk`).

## Questions

1. How can TMFOP012 call a component API, such as TMFC060's TMFxxx API, with the component's `canvasSystemRole`?
2. How can it assign and revoke a provider component's client roles (TMFC060 `knowledge-viewer`, `knowledge-editor`)
   on a consumer component's service account?

## What the Canvas does today

| Finding | Evidence |
|---|---|
| **TMFOP003 and TMFOP005-keycloak-authz hold the Keycloak master admin user and password.** They use a password grant on realm `master` with `admin-cli` | `TMFOP003-Identity-Config/keycloak/keycloakUtils.py:9-24`, `identityConfigOperatorKeycloak.py:135-136`; `dependency-management-using-keycloak-authorization/docker/src/keycloakUtils.py:10-24`; Secrets `charts/identityconfig-operator-keycloak/templates/secret.yaml:9-10`, `charts/dependency-management-keycloak-authz/templates/secret.yaml:9-10` |
| **TMFOP003 creates `canvasSystemRole` on the component's client, and a role of the same name on the `canvassystem` client. It assigns nothing.** The log line says "assigned to canvassystem client", but `add_role` POSTs to `/clients/{id}/roles` | `identityConfigOperatorKeycloak.py:226-246`, `keycloakUtils.py:133-164`. On kind, the `canvassystem` client has its own `canvasRole` role, and its service account holds **no** `kmk-knowledgemanagement` role, only realm-management `manage-clients` |
| **So no principal holds a component's `canvasSystemRole` today.** No operator calls a component API with it | Searched across the operators. TMFOP005-keycloak-authz only *reads* admin events (role mappings) to resolve dependencies (`dependencyManagementKeycloakAuthzOperator.py:1-45`) |
| **Precedent for a dedicated operator client with client credentials.** The Component Registry chart runs a post-install/upgrade hook Job with the admin password from the `canvas-keycloak` Secret. It creates the `dependentapi-operator` client, maps it `componentregistry:query`, and writes Secret `dependentapi-operator-oidc-secret` (`client_id`, `client_secret`, `token_url`). TMFOP005-simple then calls the registry with client credentials | `charts/component-registry/templates/kc-setup-hook.yaml`, `source/services/ComponentRegistry/keycloaksetup/src/kc_setup_compreg.py:39-110`; `TMFOP005…/simple-dependency-management/docker/src/oauth2_requests.py`, `component_reg_client.py:11` |
| Another precedent: a realm-import client whose secret is copied by hand into values | `charts/canvas-oda/values.yaml:104-160` (`credentialsmanagement-operator`), `charts/credentialsmanagement-operator/values.yaml:20-23` |
| Component clients have service accounts (`service-account-<componentName>`), which TMFOP005-authz relies on | `TMFOP003…/keycloakUtils.py:34-44` (`serviceAccountsEnabled: True`); `dependencyManagementKeycloakAuthzOperator.py:271-299` |
| TMFC060 accepts a token whose `aud` holds its client id and whose `resource_access[<client>].roles` holds `CANVAS_SYSTEM_ROLE` | `knowledgeApiMicroservice/app/auth.py:126-142` |

## Decision (confirmed by the user)

**The operator chart creates its own Keycloak client in a hook Job.** This is the fallback the issue names, following
the Component Registry precedent:

- A post-install/post-upgrade hook Job in `charts/dataresource-operator` uses the operator image in a setup mode. It reads the
  admin password from the `canvas-keycloak` Secret, and does three things:
  - creates the confidential client `dataresource-operator` (service account on, no browser flows);
  - maps realm-management **`view-clients`** and **`manage-users`** to its service account;
  - writes Secret `dataresource-operator-oidc-secret` (`client_id`, `client_secret`, `token_url`).

  The admin credentials exist only in the short-lived Job. Unlike the Component Registry script, the Job never logs the
  client representation, which contains the secret.
- **The operator uses only client credentials (realm `odari`).**
  - **To call a provider:** it maps the provider's `canvasSystemRole` (from the provider Component's
    `securityFunction.canvasSystemRole`, for example `canvasRole`) to **its own** service account. It does this when
    it handles a provider DataResourceConfig, and unmaps it on delete. Its next token then carries
    `resource_access[<provider>].roles: [canvasRole]` and the provider client in `aud`, through the audience-resolve
    mapper of the default `roles` scope.
  - **To grant access:** it maps `knowledge-viewer`, plus `knowledge-editor` for readWrite, on the provider's client to
    the consumer's `service-account-<consumerComponent>`, and deletes the mapping on revoke.
- `manage-users` is the narrowest built-in role that allows role mapping. Fine-grained admin permissions are only a
  preview feature on Keycloak 20. It also allows managing every user of realm `odari`, so the Secret is readable only
  by the operator's service account.

## Verified on kind (`run.sh`, 17/17)

The test used a stand-in operator client with `view-clients` + `manage-users`, and a stand-in consumer client:

- Before the self-grant, TMFC060 refuses the operator's token.
- **With its own token only, the operator:**
  - finds the provider client and its `canvasRole`, and reads its own service-account user;
  - maps `canvasRole` to itself (204).
- **Its new token** has `aud` ∋ `kmk-knowledgemanagement` and `resource_access.kmk-knowledgemanagement.roles` ∋
  `canvasRole`, with the in-cluster issuer. `GET /knowledge` and `GET /knowledgeSpecification` through the gateway
  return 200.
- **Consumer grant:** `knowledge-viewer` mapped to the consumer service account (204, visible), then revoked (204,
  gone).
- **No escalation:** mapping realm-management `manage-realm` to itself is refused (403), and creating a client is
  refused (403, no `manage-clients`).
- Both test clients deleted afterwards.

## Findings for the next issues

- **#63: Keycloak validates the token issuer against the request host.** The operator must call Keycloak at the
  in-cluster URL that TMFC060 expects as issuer
  (`http://canvas-keycloak-headless.canvas.svc.cluster.local:8083/auth`). Called through a port-forward without that
  Host header, the admin API refused the token.
- **#63: the published ExposedAPI URL can't be reached from a pod on kind.** On kind the URLs are
  `https://127.0.0.1/<component>/…`, which only works from the host. The operator needs an in-cluster route to the
  provider, such as the ExposedAPI's `spec.implementation` Service and port, or the Istio ingress Service with
  that path. ADR-010 leaves this to measurement ("Gateway Mediation").
- **#64: TMFOP005 won't resolve an `apiType: mcp` DependentAPI.** Both variants match only ExposedAPIs with
  `apiType: openapi` (`dependentApiSimpleOperator.py:198`, `dependencyManagementKeycloakAuthzOperator.py:157-169`).
  The simple operator also reads `ownerReferences[0]` as a Component (`dependentApiSimpleOperator.py:424-440`), and
  derives the component name from the DependentAPI name prefix (`:216-225`). TMFOP012 therefore has to set the
  DependentAPI status itself, or not let it be owned by a DataResourceConfig only. To be decided in #64.

## Run

```bash
helm install kmk ../chart -n components   # a TMFC060 release, Component Complete
./run.sh
```
