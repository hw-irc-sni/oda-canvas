"""Authentication and authorisation of the knowledge API.

Every operation needs a bearer JWT from the Canvas identity platform (Keycloak). Following spike S1
(hw-irc-sni/oda-canvas#70), a token is accepted when:

- its RS256 signature verifies against the in-cluster JWKS (``KEYCLOAK_JWKS_URL``; keys are cached and
  refetched when a token has an unknown ``kid``);
- ``exp``, ``nbf`` and ``iat`` hold, with ``JWT_LEEWAY_SECONDS`` of clock skew;
- ``iss`` is one of ``KEYCLOAK_ISSUERS`` (the public and the in-cluster Keycloak URL of the realm);
- ``aud`` contains this component's client id, ``COMPONENT_NAME``.

The caller's roles are ``resource_access[COMPONENT_NAME].roles``. The role named ``CANVAS_SYSTEM_ROLE``
(``canvasRole`` by default) counts as the symbolic ``canvasSystemRole`` of the OAS.

Each operation lists the roles allowed to call it in the ``x-required-roles`` extension of the TMFxxx OAS;
any one of them is enough. Routes declare their OAS ``operationId`` with ``require_operation`` and the
roles come from the OAS, which stays the single source of truth. The role matrix:

==========================================================================  =====================================================
Operations                                                                  Roles (any one)
==========================================================================  =====================================================
Read knowledge-bases: list, get, graph GET, query, search, recommend and    knowledge-viewer, knowledge-editor, canvasSystemRole
its feedback, documents, /hub
Change knowledge-bases: create, graph PUT/POST/DELETE, document upload and  knowledge-editor, canvasSystemRole
delete, ingest
Patch a knowledge-base (field changes; lifecycle transitions per            knowledge-editor, ontology-reviewer,
x-lifecycle of Knowledge)                                                   ontology-approver, canvasSystemRole
Read the ontology: modules, content, mappings, review history, validation   all five component roles and canvasSystemRole
report, /knowledgeTerm
Author: create a module, change its fields or content, create or change     ontology-editor
mappings
Review: comment, run validation                                             ontology-reviewer (editor and approver may also run
                                                                            validation)
Rule: rulings                                                               ontology-approver
Lifecycle transitions of modules and mappings                               per transition, x-lifecycle of KnowledgeSpecification
Delete a module or mapping                                                  ontology-editor (draft or rejected), ontology-approver
==========================================================================  =====================================================

A missing, malformed, expired or otherwise invalid token gets 401; a valid token without any of the
required roles gets 403.

``AUTH_ENABLED=false`` turns validation off for local development and grants every role. It is refused
when the process runs in Kubernetes (``KUBERNETES_SERVICE_HOST`` is set).
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import jwt
import yaml
from fastapi import Depends, Request

from .config import Settings
from .errors import ApiError

log = logging.getLogger(__name__)

SYSTEM_ROLE = "canvasSystemRole"
COMPONENT_ROLES = ("knowledge-viewer", "knowledge-editor", "ontology-editor", "ontology-reviewer", "ontology-approver")
ALL_ROLES = frozenset((*COMPONENT_ROLES, SYSTEM_ROLE))
HTTP_METHODS = ("get", "put", "post", "delete", "patch")


@dataclass(frozen=True)
class Principal:
    """The authenticated caller."""

    subject: str
    username: str
    roles: frozenset[str] = field(default_factory=frozenset)

    def has_any(self, roles) -> bool:
        return bool(self.roles & set(roles))


@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str
    path: str
    roles: tuple[str, ...]


def load_operations(oas_path: str) -> dict[str, Operation]:
    """Read operationId -> (method, path, x-required-roles) from the OAS. Listener callbacks are skipped."""
    oas = yaml.safe_load(Path(oas_path).read_text())
    operations = {}
    for path, item in oas.get("paths", {}).items():
        if path.startswith("/listener/"):
            continue
        for method in HTTP_METHODS:
            op = item.get(method)
            if not op:
                continue
            roles = tuple(op.get("x-required-roles") or ())
            unknown = set(roles) - ALL_ROLES
            if not roles or unknown:
                raise ValueError(f"{op.get('operationId')}: x-required-roles missing or unknown {sorted(unknown)}")
            operations[op["operationId"]] = Operation(op["operationId"], method.upper(), path, roles)
    return operations


def check_auth_settings(settings: Settings) -> None:
    """Refuse an unsafe configuration at startup."""
    if not settings.auth_enabled:
        if os.environ.get("KUBERNETES_SERVICE_HOST"):
            raise RuntimeError("AUTH_ENABLED=false is for local development only and is refused in Kubernetes")
        log.warning("AUTH_ENABLED=false: token validation is OFF and every caller has every role")
        return
    if not settings.keycloak_jwks_url or not settings.keycloak_issuers:
        raise RuntimeError("KEYCLOAK_JWKS_URL and KEYCLOAK_ISSUERS must be set when authentication is enabled")


class TokenValidator:
    """Validates bearer tokens against the Keycloak JWKS (spike S1 rules)."""

    def __init__(self, settings: Settings, jwks_client: jwt.PyJWKClient | None = None):
        self.settings = settings
        self.jwks_client = jwks_client or jwt.PyJWKClient(settings.keycloak_jwks_url, cache_keys=True,
                                                          lifespan=3600, timeout=5)

    def principal(self, token: str) -> Principal:
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.settings.component_name,
                issuer=self.settings.keycloak_issuers,
                leeway=self.settings.jwt_leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise unauthorized(f"Invalid token: {exc}") from exc
        client_roles = (claims.get("resource_access") or {}).get(self.settings.component_name, {}).get("roles", [])
        roles = {role for role in client_roles if role in COMPONENT_ROLES}
        if self.settings.canvas_system_role in client_roles:
            roles.add(SYSTEM_ROLE)
        return Principal(subject=claims["sub"], username=claims.get("preferred_username", claims["sub"]),
                         roles=frozenset(roles))


def unauthorized(message: str) -> ApiError:
    return ApiError(401, "401", "Unauthorized", message,
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "401", "Unauthorized", "A bearer token is required",
                       headers={"WWW-Authenticate": "Bearer"})
    return token.strip()


def get_principal(request: Request) -> Principal:
    """FastAPI dependency: the authenticated caller."""
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return Principal(subject="dev", username="dev", roles=ALL_ROLES)
    return request.app.state.token_validator.principal(bearer_token(request))


def require_operation(operation_id: str):
    """FastAPI dependency for a route implementing an OAS operation: it allows the roles in x-required-roles."""

    def dependency(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        operation: Operation = request.app.state.operations[operation_id]
        if not principal.has_any(operation.roles):
            raise ApiError(403, "403", "Forbidden",
                           f"This operation needs one of the roles {', '.join(operation.roles)}")
        return principal

    dependency.operation_id = operation_id
    return dependency
