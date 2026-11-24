"""Keycloak access for TMFOP012 (spike S4, hw-irc-sni/oda-canvas#61).

The operator holds only its own client credentials (client ``dataresource-operator``, realm-management
``view-clients`` + ``manage-users``), written by the chart's setup Job into a Secret mounted at ``oidc_dir``. With
them it:

- maps a provider component's ``canvasSystemRole`` to its own service account, so its token carries that role and
  the provider client in ``aud`` (TMFC060 ``auth.py`` requires both);
- maps the provider's client roles (``knowledge-viewer``, ``knowledge-editor``) to a consumer component's service
  account ``service-account-<component>``, and removes them on revoke.

Keycloak checks a token's issuer against the request host, so ``keycloak_url`` must be the in-cluster URL the
provider expects as issuer. Secrets are never logged.
"""

import asyncio
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp


class KeycloakError(Exception):
    """A Keycloak call failed; ``status`` is the HTTP status (0 for a transport error)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class CredentialsMissing(KeycloakError):
    """The client credentials Secret isn't mounted (yet)."""


def read_credentials(oidc_dir: str) -> tuple[str, str, str]:
    """Read ``client_id``, ``client_secret`` and ``token_url`` from the mounted Secret.

    Raises:
        CredentialsMissing: a key is absent or empty.
    """
    base = Path(oidc_dir)
    values = []
    for key in ("client_id", "client_secret", "token_url"):
        try:
            value = (base / key).read_text().strip()
        except OSError:
            value = ""
        if not value:
            raise CredentialsMissing(f"{key} missing in {oidc_dir}")
        values.append(value)
    return values[0], values[1], values[2]


class TokenManager:
    """Client-credentials tokens, cached until shortly before they expire.

    Args:
        oidc_dir: The mounted credentials Secret, read on every fetch.
        session_factory: Returns the aiohttp session to use.
        refresh_buffer: Seconds before expiry when a new token is fetched.
    """

    def __init__(self, oidc_dir: str, session_factory, refresh_buffer: float = 30.0, clock=time.monotonic):
        self._oidc_dir = oidc_dir
        self._session = session_factory
        self._buffer = refresh_buffer
        self._clock = clock
        self._token = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Forget the cached token, for example after a role mapping changed what it should carry."""
        self._token = None
        self._expires = 0.0

    async def token(self) -> str:
        async with self._lock:
            if self._token and self._clock() < self._expires - self._buffer:
                return self._token
            client_id, client_secret, token_url = read_credentials(self._oidc_dir)
            form = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
            try:
                async with self._session().post(token_url, data=form) as resp:
                    if resp.status != 200:
                        raise KeycloakError(f"token request refused with HTTP {resp.status}", resp.status)
                    body = await resp.json()
            except aiohttp.ClientError as e:
                raise KeycloakError(f"token request failed: {e.__class__.__name__}") from None
            self._token = body["access_token"]
            self._expires = self._clock() + float(body.get("expires_in", 60))
            return self._token


class KeycloakAdmin:
    """The admin REST calls TMFOP012 makes with its own token (realm ``realm``)."""

    def __init__(self, base_url: str, realm: str, tokens: TokenManager, session_factory):
        self._admin = f"{base_url.rstrip('/')}/admin/realms/{realm}"
        self._tokens = tokens
        self._session = session_factory

    async def _call(self, method: str, path: str, body=None, ok=(200, 204)):
        headers = {"Authorization": f"Bearer {await self._tokens.token()}"}
        try:
            async with self._session().request(method, self._admin + path, json=body, headers=headers) as resp:
                if resp.status == 404:
                    return None
                if resp.status not in ok:
                    raise KeycloakError(f"{method} {path} refused with HTTP {resp.status}", resp.status)
                if resp.status == 204 or resp.content_length == 0:
                    return {}
                return await resp.json()
        except aiohttp.ClientError as e:
            raise KeycloakError(f"{method} {path} failed: {e.__class__.__name__}") from None

    async def find_client(self, client_id: str) -> dict | None:
        """The client representation for ``clientId``, or None."""
        found = await self._call("GET", f"/clients?clientId={quote(client_id)}&search=false")
        for c in found or []:
            if c.get("clientId") == client_id:
                return c
        return None

    async def client_role(self, client_uuid: str, role: str) -> dict | None:
        return await self._call("GET", f"/clients/{client_uuid}/roles/{quote(role, safe='')}")

    async def service_account_user(self, client_uuid: str) -> dict | None:
        return await self._call("GET", f"/clients/{client_uuid}/service-account-user")

    async def mapped_client_roles(self, user_id: str, client_uuid: str) -> list[dict]:
        found = await self._call("GET", f"/users/{user_id}/role-mappings/clients/{client_uuid}")
        return found if isinstance(found, list) else []

    async def map_client_roles(self, user_id: str, client_uuid: str, roles: list[dict]) -> None:
        await self._call("POST", f"/users/{user_id}/role-mappings/clients/{client_uuid}", roles)

    async def unmap_client_roles(self, user_id: str, client_uuid: str, roles: list[dict]) -> None:
        await self._call("DELETE", f"/users/{user_id}/role-mappings/clients/{client_uuid}", roles)
