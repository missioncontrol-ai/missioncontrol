from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional
from sqlmodel import select

from app.auth.config import AuthSettings
from app.auth import OidcValidationError, OidcValidator, load_auth_settings
from app.db import get_session
from app.models import OidcAuthRequest, OidcLoginGrant
from app.routers.auth_sessions import issue_session_token
from app.services.ids import new_hash_id

router = APIRouter(prefix="/auth/oidc", tags=["auth"])
OIDC_HTTP_HEADERS = {"User-Agent": "MissionControl-OIDC/1.0 (+https://missioncontrolai.app)"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def _fetch_openid_config() -> dict:
    settings = _auth_settings()
    issuer = (settings.oidc_issuer_url or "").rstrip("/")
    if not issuer:
        raise HTTPException(status_code=503, detail="OIDC issuer is not configured")
    url = urllib_parse.urljoin(f"{issuer}/", ".well-known/openid-configuration")
    req = urllib_request.Request(url, headers=OIDC_HTTP_HEADERS)
    with urllib_request.urlopen(req, timeout=10) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload or "{}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=503, detail="OIDC discovery payload is invalid")
    return data


def _safe_redirect_path(path: str | None) -> str:
    value = (path or "").strip() or "/ui/"
    if not value.startswith("/ui"):
        return "/ui/"
    return value


def _oidc_client_id() -> str:
    settings = _auth_settings()
    client_id = (settings.oidc_client_id or "").strip()
    if client_id:
        return client_id
    raise HTTPException(status_code=503, detail="OIDC client id is not configured")


def _oidc_redirect_uri(request: Request) -> str:
    settings = _auth_settings()
    configured = (settings.oidc_redirect_uri or "").strip()
    if configured:
        return configured
    return str(request.url_for("oidc_callback"))


def _auth_settings() -> AuthSettings:
    try:
        return load_auth_settings()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OIDC auth settings are invalid: {exc}")


def _oidc_validator() -> OidcValidator:
    settings = _auth_settings()
    if not settings.oidc_enabled():
        raise HTTPException(status_code=503, detail="OIDC is not configured")
    return OidcValidator(
        issuer_url=settings.oidc_issuer_url or "",
        audience=settings.oidc_audience or "",
        jwks_url=settings.oidc_jwks_url,
    )


class OidcCliInitiateResponse(BaseModel):
    authorize_url: str
    cli_nonce: str
    expires_at: str  # ISO-8601 — CLI polls until this time


@router.get("/cli-initiate")
def cli_initiate_login(request: Request):
    """Start an OIDC login flow for the mc CLI.

    Returns the full authorize URL (for the CLI to open in a browser) and a
    cli_nonce the CLI can poll on.  No redirect — response is JSON.
    """
    settings = _auth_settings()
    if not settings.oidc_enabled():
        raise HTTPException(status_code=503, detail="OIDC is not configured on this server")

    config = _fetch_openid_config()
    authorize_endpoint = str(config.get("authorization_endpoint") or "").strip()
    if not authorize_endpoint:
        raise HTTPException(status_code=503, detail="OIDC discovery missing authorization_endpoint")

    cli_nonce = secrets.token_urlsafe(24)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    auth_id = f"oar_{new_hash_id()}"
    now = _now_utc()
    expires = now + timedelta(minutes=10)

    row = OidcAuthRequest(
        id=auth_id,
        state=state,
        nonce=nonce,
        code_verifier=verifier,
        redirect_path="/ui/",  # browser ends up here after auth; CLI doesn't care
        cli_nonce=cli_nonce,
        created_at=now,
        expires_at=expires,
    )
    with get_session() as db:
        db.add(row)
        db.commit()

    params = {
        "response_type": "code",
        "client_id": _oidc_client_id(),
        "redirect_uri": _oidc_redirect_uri(request),
        "scope": settings.oidc_scopes or "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{authorize_endpoint}?{urllib_parse.urlencode(params)}"
    return {"authorize_url": authorize_url, "cli_nonce": cli_nonce, "expires_at": expires.isoformat() + "Z"}


@router.get("/cli-poll/{cli_nonce}")
def cli_poll_login(cli_nonce: str) -> dict:
    """Poll for completion of a CLI-initiated OIDC login.

    Returns {"status":"pending"} until the browser flow completes, then
    {"status":"ready","grant_id":"olg_..."} which the CLI exchanges for a session token.
    No authentication required — the cli_nonce is the single-use secret.
    """
    now = _now_utc()
    with get_session() as db:
        grant = db.exec(
            select(OidcLoginGrant)
            .where(OidcLoginGrant.cli_nonce == cli_nonce)
            .where(OidcLoginGrant.used_at == None)  # noqa: E711
            .where(OidcLoginGrant.expires_at > now)
        ).first()
        if grant is None:
            return {"status": "pending"}
        return {"status": "ready", "grant_id": grant.id}


@router.get("/start", name="oidc_start")
def start_oidc_login(request: Request, redirect: str = Query(default="/ui/")):
    settings = _auth_settings()
    if not settings.oidc_enabled():
        raise HTTPException(status_code=503, detail="OIDC is not configured")

    config = _fetch_openid_config()
    authorize_endpoint = str(config.get("authorization_endpoint") or "").strip()
    if not authorize_endpoint:
        raise HTTPException(status_code=503, detail="OIDC discovery missing authorization_endpoint")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    auth_id = f"oar_{new_hash_id()}"
    now = _now_utc()
    row = OidcAuthRequest(
        id=auth_id,
        state=state,
        nonce=nonce,
        code_verifier=verifier,
        redirect_path=_safe_redirect_path(redirect),
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    with get_session() as db:
        db.add(row)
        db.commit()

    params = {
        "response_type": "code",
        "client_id": _oidc_client_id(),
        "redirect_uri": _oidc_redirect_uri(request),
        "scope": settings.oidc_scopes or "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(url=f"{authorize_endpoint}?{urllib_parse.urlencode(params)}", status_code=302)


@router.get("/callback", name="oidc_callback")
def oidc_callback(request: Request, code: str = Query(default=""), state: str = Query(default="")):
    settings = _auth_settings()
    validator = _oidc_validator()
    if not settings.oidc_enabled():
        raise HTTPException(status_code=503, detail="OIDC is not configured")
    if not code.strip() or not state.strip():
        raise HTTPException(status_code=400, detail="OIDC callback missing code/state")

    now = _now_utc()
    with get_session() as db:
        auth_req = db.exec(select(OidcAuthRequest).where(OidcAuthRequest.state == state)).first()
        if auth_req is None:
            raise HTTPException(status_code=400, detail="OIDC login state is invalid")
        if auth_req.used_at is not None:
            raise HTTPException(status_code=400, detail="OIDC login state already used")
        if auth_req.expires_at <= now:
            raise HTTPException(status_code=400, detail="OIDC login state expired")

        config = _fetch_openid_config()
        token_endpoint = str(config.get("token_endpoint") or "").strip()
        if not token_endpoint:
            raise HTTPException(status_code=503, detail="OIDC discovery missing token_endpoint")

        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _oidc_redirect_uri(request),
            "client_id": _oidc_client_id(),
            "code_verifier": auth_req.code_verifier,
        }
        if (settings.oidc_client_secret or "").strip():
            form["client_secret"] = settings.oidc_client_secret or ""
        token_req = urllib_request.Request(
            token_endpoint,
            data=urllib_parse.urlencode(form).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                **OIDC_HTTP_HEADERS,
            },
            method="POST",
        )
        with urllib_request.urlopen(token_req, timeout=10) as resp:
            token_payload = json.loads((resp.read() or b"{}").decode("utf-8"))
        if not isinstance(token_payload, dict):
            raise HTTPException(status_code=401, detail="OIDC token exchange failed")

        raw_token = str(token_payload.get("id_token") or token_payload.get("access_token") or "").strip()
        if not raw_token:
            raise HTTPException(status_code=401, detail="OIDC response missing token")

        try:
            principal = validator.validate_token(raw_token)
        except OidcValidationError as exc:
            raise HTTPException(status_code=401, detail=f"OIDC token validation failed: {exc.detail}")
        auth_req.used_at = now
        grant = OidcLoginGrant(
            id=f"olg_{new_hash_id()}",
            auth_request_id=auth_req.id or "",
            subject=principal.subject,
            email=principal.email or "",
            cli_nonce=auth_req.cli_nonce,  # None for browser-only flows
            created_at=now,
            expires_at=now + timedelta(minutes=3),
        )
        db.add(auth_req)
        db.add(grant)
        db.commit()

        # CLI flows get a dedicated success page with a pasteable code.
        # Browser-only flows go to the UI as before.
        if auth_req.cli_nonce:
            return RedirectResponse(
                url=f"/auth/oidc/cli-success?grant_id={urllib_parse.quote(grant.id or '')}",
                status_code=302,
            )
        separator = "&" if "?" in auth_req.redirect_path else "?"
        return RedirectResponse(
            url=f"{auth_req.redirect_path}{separator}oidc_grant={urllib_parse.quote(grant.id or '')}",
            status_code=302,
        )


_CLI_SUCCESS_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>MissionControl CLI — Authentication Complete</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0;
            display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .card {{ background: #1a1f2e; border: 1px solid #2d3748; border-radius: 12px;
             padding: 2.5rem; max-width: 480px; text-align: center; }}
    h1 {{ color: #68d391; margin-top: 0; font-size: 1.4rem; }}
    p {{ color: #a0aec0; font-size: 0.9rem; line-height: 1.6; }}
    .code {{ background: #0f1117; border: 1px solid #4a5568; border-radius: 8px;
             padding: 1rem 1.5rem; font-family: monospace; font-size: 1rem;
             letter-spacing: 0.05em; color: #f6e05e; word-break: break-all;
             user-select: all; cursor: pointer; margin: 1.2rem 0; }}
    .hint {{ font-size: 0.8rem; color: #718096; margin-top: 1.5rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>&#10003; Authentication Complete</h1>
    <p>Your browser login succeeded. If <code>mc auth login</code> hasn't continued automatically,
       copy the code below and paste it into your terminal.</p>
    <div class="code" onclick="navigator.clipboard.writeText(this.innerText)"
         title="Click to copy">{grant_id}</div>
    <p>Switch back to your terminal and paste this code when prompted.</p>
    <div class="hint">This code expires in 3 minutes and can only be used once.</div>
  </div>
</body>
</html>
"""


@router.get("/cli-success")
def cli_success_page(grant_id: str = Query(default="")):
    """Shown in the browser after a successful CLI OIDC login.

    Displays the grant_id as a pasteable one-time code for the terminal fallback.
    """
    from fastapi.responses import HTMLResponse
    safe_id = (grant_id or "").replace("<", "").replace(">", "")
    return HTMLResponse(content=_CLI_SUCCESS_HTML.format(grant_id=safe_id))


class OidcGrantExchangeRequest(BaseModel):
    grant_id: str


class OidcGrantExchangeResponse(BaseModel):
    token: str
    subject: str
    email: str | None = None
    expires_at: datetime
    session_id: int
    ttl_hours: int


@router.post("/exchange", response_model=OidcGrantExchangeResponse)
def exchange_oidc_grant(payload: OidcGrantExchangeRequest, request: Request):
    now = _now_utc()
    with get_session() as db:
        grant = db.get(OidcLoginGrant, (payload.grant_id or "").strip())
        if grant is None:
            raise HTTPException(status_code=404, detail="OIDC grant not found")
        if grant.used_at is not None:
            raise HTTPException(status_code=409, detail="OIDC grant already used")
        if grant.expires_at <= now:
            raise HTTPException(status_code=410, detail="OIDC grant expired")
        grant.used_at = now
        db.add(grant)
        db.commit()

        session_subject = (grant.email or "").strip() or grant.subject
        session = issue_session_token(
            subject=session_subject,
            user_agent=request.headers.get("user-agent", ""),
        )
        payload = session.model_dump()
        payload["email"] = (grant.email or "").strip() or None
        return OidcGrantExchangeResponse(**payload)
