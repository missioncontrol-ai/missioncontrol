from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
SESSION_COOKIE_NAME = "mc_session_token"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def _device_user_code(device_code: str) -> str:
    digest = hashlib.sha256(device_code.encode("utf-8")).hexdigest().upper()
    raw = digest[:8]
    return f"{raw[:4]}-{raw[4:]}"


def _device_verification_uri(request: Request) -> str:
    return str(request.url_for("device_verify"))


def _device_interval_seconds() -> int:
    raw = (os.getenv("OIDC_DEVICE_INTERVAL_SECONDS") or "5").strip()
    try:
        return max(2, min(int(raw), 30))
    except ValueError:
        return 5


def _device_expires_seconds() -> int:
    raw = (os.getenv("OIDC_DEVICE_EXPIRES_SECONDS") or "600").strip()
    try:
        return max(120, min(int(raw), 1800))
    except ValueError:
        return 600


def _oauth_error_response(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "error_description": description},
    )


def _public_issuer_base() -> str:
    """Public-facing issuer base URL used to rewrite browser-redirect endpoints.
    Falls back to OIDC_ISSUER_URL if OIDC_PUBLIC_ISSUER_URL is not set."""
    return (os.getenv("OIDC_PUBLIC_ISSUER_URL") or _auth_settings().oidc_issuer_url or "").rstrip("/")


def _rewrite_to_public(url: str) -> str:
    """Replace internal ClusterIP scheme+host with the public issuer scheme+host in browser-facing endpoints."""
    internal_base = (os.getenv("OIDC_INTERNAL_ISSUER_URL") or "").rstrip("/")
    public_base = _public_issuer_base()
    if not (internal_base and public_base):
        return url
    parsed_internal = urllib_parse.urlparse(internal_base)
    internal_origin = f"{parsed_internal.scheme}://{parsed_internal.netloc}"
    parsed_public = urllib_parse.urlparse(public_base)
    public_origin = f"{parsed_public.scheme}://{parsed_public.netloc}"
    if url.startswith(internal_origin):
        return public_origin + url[len(internal_origin):]
    return url


def _fetch_openid_config() -> dict:
    settings = _auth_settings()
    # OIDC_INTERNAL_ISSUER_URL allows server-side HTTP calls to use a private/ClusterIP
    # endpoint (e.g. when the public OIDC URL is not reachable from the backend container)
    # while OIDC_ISSUER_URL is still used for JWT iss validation.
    issuer = (os.getenv("OIDC_INTERNAL_ISSUER_URL") or settings.oidc_issuer_url or "").rstrip("/")
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


def _session_cookie_secure(request: Request) -> bool:
    explicit = (os.getenv("MC_SESSION_COOKIE_SECURE") or "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False
    host = (request.url.hostname or "").lower()
    return host not in {"localhost", "127.0.0.1"}


class OidcCliInitiateResponse(BaseModel):
    authorize_url: str
    cli_nonce: str
    expires_at: str  # ISO-8601 — CLI polls until this time


@router.post("/device/authorize")
async def device_authorize(request: Request):
    """RFC 8628-style device authorization endpoint."""
    settings = _auth_settings()
    if not settings.oidc_enabled():
        raise HTTPException(status_code=503, detail="OIDC is not configured on this server")

    config = _fetch_openid_config()
    authorize_endpoint = str(config.get("authorization_endpoint") or "").strip()
    if not authorize_endpoint:
        raise HTTPException(status_code=503, detail="OIDC discovery missing authorization_endpoint")

    form = await request.form()
    client_id = str(form.get("client_id") or "").strip()
    if client_id and client_id != _oidc_client_id():
        return _oauth_error_response("invalid_client", "client_id does not match configured OIDC client")

    device_code = secrets.token_urlsafe(32)
    user_code = _device_user_code(device_code)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    auth_id = f"oar_{new_hash_id()}"
    now = _now_utc()
    expires_in = _device_expires_seconds()
    interval = _device_interval_seconds()
    expires = now + timedelta(seconds=expires_in)

    row = OidcAuthRequest(
        id=auth_id,
        state=state,
        nonce=nonce,
        code_verifier=verifier,
        redirect_path=f"/auth/oidc/device/success?user_code={urllib_parse.quote(user_code)}",
        cli_nonce=device_code,
        created_at=now,
        expires_at=expires,
    )
    with get_session() as db:
        db.add(row)
        db.commit()

    verification_uri = _device_verification_uri(request)
    verification_uri_complete = f"{verification_uri}?user_code={urllib_parse.quote(user_code)}"
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "verification_uri_complete": verification_uri_complete,
        "expires_in": expires_in,
        "interval": interval,
        "authorization_endpoint": authorize_endpoint,
    }


@router.get("/device/verify", name="device_verify")
def device_verify(request: Request, user_code: str = Query(default="")):
    """Browser entrypoint for device flow (user enters code and authenticates)."""
    code = (user_code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="user_code is required")
    now = _now_utc()
    with get_session() as db:
        rows = db.exec(
            select(OidcAuthRequest)
            .where(OidcAuthRequest.cli_nonce != None)  # noqa: E711
            .where(OidcAuthRequest.used_at == None)  # noqa: E711
            .where(OidcAuthRequest.expires_at > now)
        ).all()
        match = next(
            (row for row in rows if _device_user_code(str(row.cli_nonce or "")).upper() == code),
            None,
        )
        if match is None:
            raise HTTPException(status_code=400, detail="user_code is invalid or expired")

    config = _fetch_openid_config()
    authorize_endpoint = str(config.get("authorization_endpoint") or "").strip()
    if not authorize_endpoint:
        raise HTTPException(status_code=503, detail="OIDC discovery missing authorization_endpoint")
    params = {
        "response_type": "code",
        "client_id": _oidc_client_id(),
        "redirect_uri": _oidc_redirect_uri(request),
        "scope": _auth_settings().oidc_scopes or "openid profile email",
        "state": match.state,
        "nonce": match.nonce,
        "code_challenge": _b64url(hashlib.sha256(match.code_verifier.encode("utf-8")).digest()),
        "code_challenge_method": "S256",
    }
    return RedirectResponse(url=f"{_rewrite_to_public(authorize_endpoint)}?{urllib_parse.urlencode(params)}", status_code=302)


@router.post("/device/token")
async def device_token(request: Request):
    """RFC 8628-style token polling endpoint."""
    form = await request.form()
    grant_type = str(form.get("grant_type") or "").strip()
    if grant_type != DEVICE_GRANT_TYPE:
        return _oauth_error_response("unsupported_grant_type", "grant_type must be device_code")
    device_code = str(form.get("device_code") or "").strip()
    if not device_code:
        return _oauth_error_response("invalid_request", "device_code is required")
    now = _now_utc()
    with get_session() as db:
        auth_req = db.exec(
            select(OidcAuthRequest)
            .where(OidcAuthRequest.cli_nonce == device_code)
        ).first()
        if auth_req is None:
            return _oauth_error_response("invalid_grant", "device_code is invalid")
        if auth_req.expires_at <= now:
            return _oauth_error_response("expired_token", "device_code expired")

        grant = db.exec(
            select(OidcLoginGrant)
            .where(OidcLoginGrant.cli_nonce == device_code)
            .where(OidcLoginGrant.expires_at > now)
        ).first()
        if grant is None:
            return _oauth_error_response("authorization_pending", "authorization is still pending")
        if grant.used_at is not None:
            return _oauth_error_response("invalid_grant", "grant already used")

        grant.used_at = now
        db.add(grant)
        db.commit()

        session_subject = (grant.email or "").strip() or grant.subject
        session = issue_session_token(
            subject=session_subject,
            user_agent=request.headers.get("user-agent", ""),
        )
        expires_in = max(1, int((session.expires_at - now).total_seconds()))
        return {
            "access_token": session.token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": _auth_settings().oidc_scopes or "openid profile email",
        }


@router.get("/device/success")
def device_success_page(user_code: str = Query(default="")):
    safe_code = (user_code or "").replace("<", "").replace(">", "")
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>MissionControl Device Login Complete</title></head>
<body style="font-family:system-ui,sans-serif;padding:2rem;background:#0f1117;color:#e2e8f0;">
  <h2>Authentication Complete</h2>
  <p>You can return to your terminal/device. Code: <code>{safe_code}</code></p>
</body></html>"""
    return HTMLResponse(content=html)


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
    authorize_url = f"{_rewrite_to_public(authorize_endpoint)}?{urllib_parse.urlencode(params)}"
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
    return RedirectResponse(url=f"{_rewrite_to_public(authorize_endpoint)}?{urllib_parse.urlencode(params)}", status_code=302)


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
        # Use URL fragment so grant does not traverse server-side logs/referrers.
        separator = "&" if "#" in auth_req.redirect_path else "#"
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
def exchange_oidc_grant(payload: OidcGrantExchangeRequest, request: Request, response: Response):
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
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session.token,
            httponly=True,
            secure=_session_cookie_secure(request),
            samesite="lax",
            path="/",
        )
        payload = session.model_dump()
        payload["email"] = (grant.email or "").strip() or None
        return OidcGrantExchangeResponse(**payload)
