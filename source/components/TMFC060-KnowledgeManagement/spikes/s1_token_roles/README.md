# Spike S1: component client roles in the Canvas Portal access token

Issue: [hw-irc-sni/oda-canvas#70](https://github.com/hw-irc-sni/oda-canvas/issues/70). Run on 2026-11-22 on the local kind
Canvas (canvas-oda 1.2.6-rc8, canvas-portal 0.1.3), with the user's authorisation to use the Keycloak admin credentials.
Re-run with `./run.sh`; it cleans up after itself.

## Question

The portal logs users in with a password grant on the `canvas-portal` client. Does that access token carry a TMFC060
instance's client roles, so that the component can authorise the user's forwarded token? Which audience and issuer
does it have, and does the realm mismatch (chart `odari`, frontend `.env` `canvas`) matter?

## Answer: yes

| Check | Result |
|---|---|
| Realms on the Canvas | `master`, `odari`. There is no `canvas` realm, so the frontend `.env` value is unused; the portal chart uses `odari` |
| `canvas-portal` client | `publicClient: true`, `fullScopeAllowed: true`, `directAccessGrantsEnabled: true`, default client scopes `web-origins, acr, roles, profile, email`, one protocol mapper (`groups`) |
| Component client | The identity operator created `s1km-knowledgemanagement` with roles `knowledge-viewer`, `knowledge-editor`, `ontology-editor`, `ontology-reviewer`, `ontology-approver` and `canvasRole` (the chart's `security.canvasSystemRole` value) |
| Token of a user holding `knowledge-viewer` on that client | `resource_access["s1km-knowledgemanagement"].roles = ["knowledge-viewer"]`; `aud = ["s1km-knowledgemanagement", "account"]`; `azp = "canvas-portal"`; `iss` = the Keycloak URL the token was requested from ([`claims-viewer.json`](claims-viewer.json)) |

The default `roles` client scope puts the user's client roles into `resource_access`, and its audience resolver adds
every client the user has roles on to `aud`. Because `canvas-portal` has full scope, no extra mapper is needed.

## Mechanism for `auth.py` (#23, chosen by the user)

- Verify the RS256 signature with keys from the **in-cluster JWKS URL** (`KEYCLOAK_JWKS_URL`). Cache the keys, and
  refresh them when a token has an unknown `kid`.
- Accept `iss` from a **configured list** (`KEYCLOAK_ISSUERS`): the public URL the portal uses, and the in-cluster URL.
- **Require the component's own client id** (`COMPONENT_NAME`, for example `s1km-knowledgemanagement`) **in `aud`**.
- Read the roles from `resource_access[COMPONENT_NAME].roles`. The symbolic `canvasSystemRole` of the OAS
  `x-required-roles` is satisfied by the role named in `CANVAS_SYSTEM_ROLE` (default `canvasRole`, set by the chart).

Operator tokens (spike S4, #61) must also carry the component's client id in `aud`.
