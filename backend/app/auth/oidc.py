import json
import time
from dataclasses import dataclass
from urllib import request
from urllib.parse import urljoin

import jwt
from jwt import InvalidTokenError, PyJWKClient


class OidcValidationError(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass
class OidcPrincipal:
    subject: str
    email: str | None
    claims: dict


class OidcValidator:
    def __init__(
        self,
        issuer_url: str,
        audience: str,
        jwks_url: str | None = None,
        metadata_ttl_seconds: int = 3600,
    ):
        self.issuer_url = issuer_url.rstrip("/")
        self.audience = audience
        self._explicit_jwks_url = jwks_url
        self._metadata_ttl_seconds = metadata_ttl_seconds
        self._metadata_expiry = 0.0
        self._cached_jwks_url: str | None = jwks_url
        self._jwk_client: PyJWKClient | None = PyJWKClient(jwks_url) if jwks_url else None

    def _openid_config_url(self) -> str:
        return urljoin(f"{self.issuer_url}/", ".well-known/openid-configuration")

    def _load_openid_config(self) -> dict:
        url = self._openid_config_url()
        with request.urlopen(url, timeout=10) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)

    def _get_jwks_url(self) -> str:
        now = time.time()
        if self._explicit_jwks_url:
            return self._explicit_jwks_url

        if self._cached_jwks_url and now < self._metadata_expiry:
            return self._cached_jwks_url

        config = self._load_openid_config()
        jwks_uri = config.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri.strip():
            raise OidcValidationError("oidc_validation_failed", "OIDC discovery missing jwks_uri")

        self._cached_jwks_url = jwks_uri
        self._metadata_expiry = now + self._metadata_ttl_seconds
        return jwks_uri

    def _get_jwk_client(self) -> PyJWKClient:
        jwks_url = self._get_jwks_url()
        if self._jwk_client is None or self._jwk_client.uri != jwks_url:
            self._jwk_client = PyJWKClient(jwks_url)
        return self._jwk_client

    def validate_token(self, token: str) -> OidcPrincipal:
        try:
            signing_key = self._get_jwk_client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=self.audience,
                issuer=self.issuer_url,
            )
        except OidcValidationError:
            raise
        except InvalidTokenError as exc:
            raise OidcValidationError("oidc_validation_failed", str(exc)) from exc
        except Exception as exc:
            raise OidcValidationError("oidc_validation_failed", f"OIDC validation error: {exc}") from exc

        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise OidcValidationError("oidc_validation_failed", "Token missing subject (sub)")

        email = claims.get("email")
        if email is not None:
            email = str(email)

        return OidcPrincipal(subject=subject, email=email, claims=claims)
