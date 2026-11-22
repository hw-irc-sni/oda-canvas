"""Locally signed RS256 tokens and an in-process JWKS, for the auth tests."""
import json
import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://keycloak.example.org/auth/realms/odari"
INTERNAL_ISSUER = "http://canvas-keycloak.canvas.svc.cluster.local:8083/auth/realms/odari"
CLIENT = "km-knowledgemanagement"


class KeyPair:
    def __init__(self, kid: str | None = None):
        self.kid = kid or uuid.uuid4().hex
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(self) -> dict:
        key = json.loads(RSAAlgorithm.to_jwk(self.private.public_key()))
        return {**key, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def token(self, roles=("knowledge-viewer",), *, client=CLIENT, iss=ISSUER, aud=None, exp_in=300,
              alg="RS256", extra=None) -> str:
        now = int(time.time())
        claims = {
            "iss": iss, "sub": "user-1", "preferred_username": "alice", "iat": now, "exp": now + exp_in,
            "aud": aud if aud is not None else [client, "account"], "azp": "canvas-portal",
            "resource_access": {client: {"roles": list(roles)}, "account": {"roles": ["view-profile"]}},
        }
        claims.update(extra or {})
        if alg == "HS256":
            return jwt.encode(claims, "shared-secret-that-is-at-least-32-bytes-long", algorithm="HS256",
                              headers={"kid": self.kid})
        return jwt.encode(claims, self.private, algorithm=alg, headers={"kid": self.kid})


class StaticJWKClient(jwt.PyJWKClient):
    """A PyJWKClient whose JWKS is served from memory instead of over HTTP."""

    def __init__(self, *keys: KeyPair):
        super().__init__("http://jwks.invalid/certs", cache_keys=True)
        self.keys = list(keys)
        self.fetches = 0

    def fetch_data(self):
        self.fetches += 1
        return {"keys": [k.jwk() for k in self.keys]}
