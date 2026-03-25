import json
import asyncio
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, parse_qsl, urlparse

from fastapi import HTTPException
from fastapi import Response
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel

from app.auth.config import AuthSettings
from app.db import engine
from app.routers import oidc_web


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _request():
    return SimpleNamespace(
        headers={"user-agent": "pytest"},
        cookies={},
        url=SimpleNamespace(hostname="localhost"),
        url_for=lambda name: "http://localhost:8008/auth/oidc/callback",
    )


class _Validator:
    def validate_token(self, _token: str):
        return SimpleNamespace(subject="user@example.com", email="user@example.com")


class OidcWebTests(unittest.TestCase):
    @staticmethod
    def _await(value):
        if hasattr(value, "__await__"):
            return asyncio.run(value)
        return value

    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        self._orig_settings = oidc_web._auth_settings
        self._orig_validator = oidc_web._oidc_validator
        self._orig_discovery = oidc_web._fetch_openid_config
        self._orig_urlopen = oidc_web.urllib_request.urlopen

        oidc_web._auth_settings = lambda: AuthSettings(
            mode="dual",
            missioncontrol_token="test-token",
            oidc_issuer_url="https://idp.example.com",
            oidc_audience="test-client",
            oidc_jwks_url=None,
            oidc_client_id="test-client",
            oidc_client_secret="",
            oidc_redirect_uri="http://localhost:8008/auth/oidc/callback",
            oidc_scopes="openid profile email",
            oidc_required=False,
        )
        oidc_web._oidc_validator = lambda: _Validator()
        oidc_web._fetch_openid_config = lambda: {
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
        }
        oidc_web.urllib_request.urlopen = lambda req, timeout=10: _FakeResponse({"id_token": "jwt"})

    def tearDown(self):
        oidc_web._auth_settings = self._orig_settings
        oidc_web._oidc_validator = self._orig_validator
        oidc_web._fetch_openid_config = self._orig_discovery
        oidc_web.urllib_request.urlopen = self._orig_urlopen

    def test_oidc_start_callback_exchange(self):
        req = _request()
        start = oidc_web.start_oidc_login(req, redirect="/ui/")
        self.assertEqual(start.status_code, 302)
        query = parse_qs(urlparse(start.headers["location"]).query)
        state = query["state"][0]
        self.assertTrue(state)

        cb = oidc_web.oidc_callback(req, code="abc", state=state)
        self.assertEqual(cb.status_code, 302)
        cb_fragment = dict(parse_qsl(urlparse(cb.headers["location"]).fragment))
        grant_id = cb_fragment["oidc_grant"]
        self.assertTrue(grant_id.startswith("olg_"))

        response = Response()
        first = oidc_web.exchange_oidc_grant(oidc_web.OidcGrantExchangeRequest(grant_id=grant_id), req, response)
        self.assertTrue(first.token.startswith("mcs_"))
        self.assertEqual(first.subject, "user@example.com")
        self.assertIn("mc_session_token=", response.headers.get("set-cookie", ""))

        with self.assertRaises(HTTPException) as ctx:
            oidc_web.exchange_oidc_grant(oidc_web.OidcGrantExchangeRequest(grant_id=grant_id), req, Response())
        self.assertEqual(ctx.exception.status_code, 409)

    def test_device_authorize_and_token_pending(self):
        req = _request()

        class _DeviceRequest:
            async def form(self):
                return {"client_id": "test-client"}

            headers = {"user-agent": "pytest"}
            cookies = {}
            url = SimpleNamespace(hostname="localhost")
            url_for = lambda self, name: "http://localhost:8008/auth/oidc/device/verify"

        authorize = self._await(oidc_web.device_authorize(_DeviceRequest()))
        self.assertIn("device_code", authorize)
        self.assertIn("user_code", authorize)
        self.assertEqual(authorize["verification_uri"], "http://localhost:8008/auth/oidc/device/verify")

        class _TokenRequest:
            def __init__(self, code: str):
                self._code = code
                self.headers = {"user-agent": "pytest"}

            async def form(self):
                return {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": self._code,
                }

        token_resp = self._await(oidc_web.device_token(_TokenRequest(authorize["device_code"])))
        self.assertIsInstance(token_resp, JSONResponse)
        payload = json.loads(token_resp.body.decode("utf-8"))
        self.assertEqual(payload.get("error"), "authorization_pending")


if __name__ == "__main__":
    unittest.main()
