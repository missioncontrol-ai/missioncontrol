import asyncio
from pathlib import Path
import hmac
import json
import os
import secrets
import time
import uuid
from collections import deque
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from app.auth import OidcValidationError, OidcValidator, load_auth_settings
from app.db import init_db
from app.db import get_session
from app.routers import auth_sessions
from app.routers import oidc_web
from app.services.authz import assert_platform_admin
from app.services.log_export import emit_structured_log, recent_logs
from app.services.telemetry import telemetry
from app.services.mqtt import build_mqtt_service
from app.services.object_storage import head_bucket, object_storage_enabled
from app.services.schema_pack import load_schema_pack
from app.services.governance import ensure_governance_policy_seed
import app.ai_console.registry  # noqa: F401 — bootstraps adapter registry at import time

from app.routers import (
    klusters,
    missions,
    docs,
    artifacts,
    tasks,
    mcp,
    ingestion,
    search,
    agents,
    schema_pack,
    explorer,
    onboarding,
    governance,
    approvals,
    slack_integrations,
    runtime,
    feedback,
    skills,
    google_chat_integrations,
    teams_integrations,
    chat_integrations,
    ops,
    profiles,
    evolve,
    ai,
    scheduled_jobs,
    persistence,
    remotectl,
    hooks,
)

app = FastAPI(
    title="AI Mission Control",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

AUTH_SETTINGS = load_auth_settings()
OIDC_VALIDATOR = (
    OidcValidator(
        issuer_url=AUTH_SETTINGS.oidc_issuer_url or "",
        audience=AUTH_SETTINGS.oidc_audience or "",
        jwks_url=AUTH_SETTINGS.oidc_jwks_url,
    )
    if AUTH_SETTINGS.oidc_enabled()
    else None
)
AUTH_EXEMPT_PATHS = {"/", "/api/openapi.json", "/agent-onboarding.json", "/healthz", "/readyz"}
AUTH_EXEMPT_PREFIXES = (
    "/api/docs",
    "/api/redoc",
    "/ui",
    "/auth/oidc",
)
AUTH_EXEMPT_WEBHOOK_PATHS = {
    "/integrations/slack/events",
    "/integrations/slack/commands",
    "/integrations/slack/interactions",
    "/integrations/google-chat/events",
    "/integrations/teams/events",
}
TOKEN_SUBJECT = "token-client"
CSRF_COOKIE_NAME = "mc_csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"


def _as_int(value: str | None, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        parsed = int((value or "").strip())
    except ValueError:
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _as_float(value: str | None, default: float, *, minimum: float = 0.1) -> float:
    try:
        parsed = float((value or "").strip())
    except ValueError:
        parsed = default
    return max(minimum, parsed)


def _token_fallback_path_prefixes() -> tuple[str, ...]:
    """Optional path prefixes that may accept static-token fallback in dual mode.

    Default is disabled (empty) so OIDC-required deployments do not accidentally
    expose fallback static-token auth on mutation-capable routes.
    """
    raw = (os.getenv("MC_ALLOW_TOKEN_FALLBACK_PATHS") or "").strip()
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _trusted_proxy_ips() -> set[str]:
    raw = (os.getenv("MC_TRUSTED_PROXY_IPS") or "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _session_cookie_secure(request: Request) -> bool:
    explicit = (os.getenv("MC_SESSION_COOKIE_SECURE") or "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False
    host = (request.url.hostname or "").lower()
    return host not in {"localhost", "127.0.0.1"}


@dataclass(frozen=True)
class RateLimitPolicy:
    capacity: int
    window_seconds: float


class InMemoryRateLimiter:
    def __init__(self):
        self._buckets: dict[str, deque[float]] = {}

    def allow(self, key: str, policy: RateLimitPolicy, now: float) -> bool:
        bucket = self._buckets.setdefault(key, deque())
        threshold = now - policy.window_seconds
        while bucket and bucket[0] <= threshold:
            bucket.popleft()
        if len(bucket) >= policy.capacity:
            return False
        bucket.append(now)
        return True


RATE_LIMITER = InMemoryRateLimiter()
DEFAULT_RATE_POLICY = RateLimitPolicy(
    capacity=_as_int(os.getenv("MC_RATE_LIMIT_DEFAULT_CAPACITY"), 240, maximum=10_000),
    window_seconds=_as_float(os.getenv("MC_RATE_LIMIT_DEFAULT_WINDOW_SECONDS"), 60.0),
)
SEARCH_RATE_POLICY = RateLimitPolicy(
    capacity=_as_int(os.getenv("MC_RATE_LIMIT_SEARCH_CAPACITY"), 60, maximum=10_000),
    window_seconds=_as_float(os.getenv("MC_RATE_LIMIT_SEARCH_WINDOW_SECONDS"), 60.0),
)
WRITE_RATE_POLICY = RateLimitPolicy(
    capacity=_as_int(os.getenv("MC_RATE_LIMIT_WRITE_CAPACITY"), 120, maximum=10_000),
    window_seconds=_as_float(os.getenv("MC_RATE_LIMIT_WRITE_WINDOW_SECONDS"), 60.0),
)
APPROVAL_RATE_POLICY = RateLimitPolicy(
    capacity=_as_int(os.getenv("MC_RATE_LIMIT_APPROVAL_CAPACITY"), 30, maximum=10_000),
    window_seconds=_as_float(os.getenv("MC_RATE_LIMIT_APPROVAL_WINDOW_SECONDS"), 60.0),
)
TIMEOUT_SECONDS = _as_float(os.getenv("MC_REQUEST_TIMEOUT_SECONDS"), 30.0)


def _rate_limit_policy(request: Request) -> RateLimitPolicy:
    path = request.url.path
    if path.startswith("/approvals") or path.startswith("/governance"):
        return APPROVAL_RATE_POLICY
    if path.startswith("/search") or path.startswith("/explorer"):
        return SEARCH_RATE_POLICY
    if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
        return WRITE_RATE_POLICY
    return DEFAULT_RATE_POLICY


def _rate_limit_key(request: Request) -> str:
    principal = getattr(getattr(request, "state", None), "principal", None)
    if isinstance(principal, dict):
        subject = str(principal.get("email") or principal.get("subject") or "").strip().lower()
        if subject:
            return f"principal:{subject}"
    client_host = getattr(getattr(request, "client", None), "host", None) or "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    trusted_proxies = _trusted_proxy_ips()
    if trusted_proxies and client_host in trusted_proxies and forwarded.strip():
        ip = forwarded.split(",", 1)[0].strip()
    else:
        ip = client_host
    return f"ip:{ip}"


def _is_auth_exempt_path(path: str) -> bool:
    return (
        path in AUTH_EXEMPT_PATHS
        or path in AUTH_EXEMPT_WEBHOOK_PATHS
        or any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES)
    )


def _cors_allow_origins() -> list[str]:
    raw = (os.getenv("MC_CORS_ALLOW_ORIGINS") or "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [
        "http://localhost:7007",
        "http://127.0.0.1:7007",
        "http://localhost:8008",
        "http://127.0.0.1:8008",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    with get_session() as session:
        ensure_governance_policy_seed(session)
    app.state.schema_pack = load_schema_pack()
    mqtt_service = build_mqtt_service()
    if mqtt_service is not None:
        mqtt_service.connect()
    app.state.mqtt = mqtt_service
    from app.services.agent_scheduler import start_scheduler
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    mqtt_service = getattr(app.state, "mqtt", None)
    if mqtt_service is not None:
        mqtt_service.disconnect()
    from app.services.agent_scheduler import stop_scheduler
    stop_scheduler()


@app.middleware("http")
async def require_auth(request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()

    if request.method == "OPTIONS":
        response = await call_next(request)
        return _finalize_response(request, response, start)

    path = request.url.path
    if _is_auth_exempt_path(path):
        response = await call_next(request)
        return _finalize_response(request, response, start)

    auth_header = request.headers.get("authorization")
    bearer_token = _extract_bearer_token(auth_header)
    cookie_token = _extract_session_cookie_token(request)
    token = bearer_token or cookie_token
    if not token:
        return _unauthorized(
            request,
            "missing_token",
            "Unauthorized: bearer token or session cookie is required",
            start=start,
        )

    # Session tokens (mcs_*) are validated against the DB regardless of AUTH_MODE.
    if token.startswith("mcs_"):
        return await _require_session_token(
            request,
            token,
            call_next,
            start,
            token_from_cookie=bool(cookie_token and not bearer_token),
        )

    if AUTH_SETTINGS.mode == "token":
        if not AUTH_SETTINGS.missioncontrol_token or not hmac.compare_digest(token, AUTH_SETTINGS.missioncontrol_token):
            return _unauthorized(request, "invalid_token", "Unauthorized: invalid bearer token", start=start)
        request.state.principal = {
            "subject": TOKEN_SUBJECT,
            "email": None,
            "auth_type": "token",
        }
        response = await _call_next_with_guards(request, call_next)
        return _finalize_response(request, response, start)

    if AUTH_SETTINGS.mode == "oidc":
        return await _require_oidc_token(request, token, call_next, start)

    # AUTH_MODE=dual
    token_fallback_allowed = not AUTH_SETTINGS.oidc_required
    if not token_fallback_allowed:
        token_fallback_allowed = any(
            path.startswith(prefix) for prefix in _token_fallback_path_prefixes()
        )
    if (
        token_fallback_allowed
        and AUTH_SETTINGS.token_enabled()
        and AUTH_SETTINGS.missioncontrol_token
        and hmac.compare_digest(token, AUTH_SETTINGS.missioncontrol_token)
    ):
        request.state.principal = {
            "subject": TOKEN_SUBJECT,
            "email": None,
            "auth_type": "token",
        }
        response = await _call_next_with_guards(request, call_next)
        return _finalize_response(request, response, start)

    if OIDC_VALIDATOR is not None:
        try:
            principal = OIDC_VALIDATOR.validate_token(token)
            request.state.principal = {
                "subject": principal.subject,
                "email": principal.email,
                "auth_type": "oidc",
            }
            response = await _call_next_with_guards(request, call_next)
            return _finalize_response(request, response, start)
        except OidcValidationError as exc:
            if AUTH_SETTINGS.oidc_required:
                return _unauthorized(request, exc.reason, f"Unauthorized: {exc.detail}", start=start)
            # In dual mode without strict OIDC, surface whichever mode failed most specifically.
            if not AUTH_SETTINGS.token_enabled():
                return _unauthorized(request, exc.reason, f"Unauthorized: {exc.detail}", start=start)

    return _unauthorized(request, "invalid_token", "Unauthorized: missing or invalid bearer token", start=start)


def _extract_bearer_token(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    parts = auth_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token if token else None


def _extract_session_cookie_token(request: Request) -> str | None:
    token = str(request.cookies.get("mc_session_token") or "").strip()
    if token.startswith("mcs_"):
        return token
    return None


async def _require_oidc_token(request, token: str, call_next, start: float):
    if OIDC_VALIDATOR is None:
        return _unauthorized(request, "oidc_validation_failed", "Unauthorized: OIDC is not configured", start=start)
    try:
        principal = OIDC_VALIDATOR.validate_token(token)
    except OidcValidationError as exc:
        return _unauthorized(request, exc.reason, f"Unauthorized: {exc.detail}", start=start)

    request.state.principal = {
        "subject": principal.subject,
        "email": principal.email,
        "auth_type": "oidc",
    }
    response = await _call_next_with_guards(request, call_next)
    return _finalize_response(request, response, start)


async def _require_session_token(request, token: str, call_next, start: float, *, token_from_cookie: bool = False):
    import hashlib
    from datetime import datetime as _dt
    from sqlmodel import select as _select
    from app.models import UserSession

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = _dt.utcnow()
    session_row = None
    with get_session() as db:
        row = db.exec(
            _select(UserSession)
            .where(UserSession.token_hash == token_hash)
            .where(UserSession.revoked == False)  # noqa: E712
            .where(UserSession.expires_at > now)
        ).first()
        if row is None:
            return _unauthorized(
                request,
                "invalid_session",
                "Unauthorized: invalid or expired session token",
                start=start,
            )
        session_row = {
            "subject": row.subject,
            "session_id": row.id,
            "expires_at": row.expires_at,
            "stale_last_used": (now - row.last_used_at).total_seconds() > 300,
            "row_id": row.id,
        }
        # Update last_used_at if stale (> 5 min) to avoid a write on every request
        if session_row["stale_last_used"]:
            row.last_used_at = now
            db.add(row)
            db.commit()

    request.state.principal = {
        "subject": session_row["subject"],
        "email": session_row["subject"] if "@" in session_row["subject"] else None,
        "auth_type": "session",
        "session_id": session_row["session_id"],
        "session_expires_at": session_row["expires_at"],
    }
    request.state.auth_via_cookie = bool(token_from_cookie)
    response = await _call_next_with_guards(request, call_next)
    if token_from_cookie:
        _ensure_csrf_cookie(request, response)
    return _finalize_response(request, response, start)


async def _call_next_with_guards(request: Request, call_next):
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and _requires_csrf(request):
        if not _validate_csrf(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden", "reason": "csrf_invalid"},
            )
    now = time.monotonic()
    policy = _rate_limit_policy(request)
    key = f"{request.method}:{request.url.path}:{_rate_limit_key(request)}"
    if not RATE_LIMITER.allow(key, policy, now):
        return JSONResponse(
            status_code=429,
            content={"detail": "Too Many Requests", "reason": "rate_limited"},
            headers={"retry-after": str(max(1, int(policy.window_seconds)))},
        )
    try:
        return await asyncio.wait_for(call_next(request), timeout=TIMEOUT_SECONDS)
    except TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"detail": "Gateway Timeout", "reason": "request_timeout"},
        )


def _unauthorized(request: Request, reason: str, detail: str, *, start: float) -> JSONResponse:
    response = JSONResponse(status_code=401, content={"detail": detail, "reason": reason})
    return _finalize_response(request, response, start)


def _finalize_response(request: Request, response, start: float):
    _apply_security_headers(response)
    response = _attach_trace_headers(request, response)
    request_id = _request_id(request)
    response.headers["x-request-id"] = request_id

    duration_ms = int((time.perf_counter() - start) * 1000)
    route_obj = request.scope.get("route")
    endpoint = str(getattr(route_obj, "path", request.url.path))
    mcp_tool = getattr(getattr(request, "state", None), "mcp_tool", None)
    if endpoint == "/mcp/call" and isinstance(mcp_tool, str) and mcp_tool.strip():
        endpoint = f"{endpoint}:{mcp_tool.strip()}"
    principal = getattr(getattr(request, "state", None), "principal", None)
    actor_type = "anonymous"
    actor_subject = ""
    if isinstance(principal, dict):
        actor_type = str(principal.get("auth_type") or "authenticated")
        actor_subject = str(principal.get("email") or principal.get("subject") or "")
    mission_id = _extract_mission_id(request)
    telemetry.end_sample(
        method=request.method,
        endpoint=endpoint,
        status=int(getattr(response, "status_code", 500)),
        latency_ms=duration_ms,
        actor_type=actor_type,
        mission_id=mission_id,
    )
    emit_structured_log(
        {
            "event_type": "http.request",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "endpoint": endpoint,
            "status": int(getattr(response, "status_code", 500)),
            "duration_ms": duration_ms,
            "actor_type": actor_type,
            "actor_subject": actor_subject,
            "mission_id": mission_id or "",
            "channel": "mcp" if request.url.path.startswith("/mcp") else "api",
        }
    )
    return response


def _requires_csrf(request: Request) -> bool:
    return bool(getattr(getattr(request, "state", None), "auth_via_cookie", False))


def _validate_csrf(request: Request) -> bool:
    cookie_token = str(request.cookies.get(CSRF_COOKIE_NAME) or "").strip()
    header_token = str(request.headers.get(CSRF_HEADER_NAME) or "").strip()
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


def _ensure_csrf_cookie(request: Request, response) -> None:
    current = str(request.cookies.get(CSRF_COOKIE_NAME) or "").strip()
    token = current or secrets.token_urlsafe(24)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=_session_cookie_secure(request),
        samesite="lax",
        path="/",
    )


def _apply_security_headers(response) -> None:
    response.headers.setdefault(
        "content-security-policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    response.headers.setdefault("referrer-policy", "no-referrer")
    response.headers.setdefault("x-frame-options", "DENY")
    response.headers.setdefault(
        "permissions-policy",
        "geolocation=(), microphone=(), camera=()",
    )


def _request_id(request: Request) -> str:
    state = getattr(request, "state", None)
    value = getattr(state, "request_id", None) if state is not None else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    generated = str(uuid.uuid4())
    if state is not None:
        setattr(state, "request_id", generated)
    return generated


def _extract_mission_id(request: Request) -> str | None:
    path_params = getattr(request, "path_params", None)
    if isinstance(path_params, dict):
        mission_id = path_params.get("mission_id")
        if mission_id is not None and str(mission_id).strip():
            return str(mission_id).strip()
    query_mission_id = request.query_params.get("mission_id")
    if query_mission_id and query_mission_id.strip():
        return query_mission_id.strip()
    return None


def _attach_trace_headers(request, response):
    trace = getattr(getattr(request, "state", None), "approval_trace", None)
    if not isinstance(trace, dict):
        return response
    approval_request_id = trace.get("approval_request_id")
    approval_nonce = trace.get("approval_nonce")
    if approval_request_id:
        response.headers["x-approval-request-id"] = str(approval_request_id)
    if approval_nonce:
        response.headers["x-approval-nonce"] = str(approval_nonce)
    return response


@app.get("/")
def root():
    return {"status": "ok", "service": "ai-missioncontrol"}


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "ai-missioncontrol"}


@app.get("/readyz")
def readyz():
    with get_session() as session:
        session.exec(text("SELECT 1"))
    if object_storage_enabled():
        head_bucket()
    mqtt_service = getattr(app.state, "mqtt", None)
    mqtt_required = str(os.getenv("MQTT_OPTIONAL", "true")).strip().lower() not in {"1", "true", "yes", "on"}
    if mqtt_required and mqtt_service is not None and not mqtt_service.connected:
        return JSONResponse(status_code=503, content={"status": "degraded", "reason": "mqtt_unavailable"})
    return {"status": "ok", "service": "ai-missioncontrol"}


@app.get("/ops/metrics")
def ops_metrics(request: Request):
    assert_platform_admin(request)
    return telemetry.snapshot()


@app.get("/ops/logs")
def ops_logs(request: Request, limit: int = 200):
    assert_platform_admin(request)
    return {"events": recent_logs(limit=limit)}


@app.get("/events/stream")
async def matrix_stream(request: Request, limit: int = 50):
    safe_limit = max(1, min(int(limit), 200))

    async def generator():
        last_signature: tuple[str, str] | None = None
        while True:
            if await request.is_disconnected():
                break
            # Stream the most recent structured logs so dashboards and `mc system doctor`
            # can observe live matrix telemetry from a stable SSE endpoint.
            emitted = False
            for item in recent_logs(limit=safe_limit):
                signature = (
                    str(item.get("timestamp") or ""),
                    str(item.get("request_id") or item.get("event_type") or ""),
                )
                if last_signature is not None and signature <= last_signature:
                    continue
                payload = {"type": "matrix", "payload": item}
                yield "event: matrix\n"
                yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                emitted = True
                last_signature = signature
            if not emitted:
                yield "event: matrix\n"
                yield "data: " + json.dumps(
                    {"type": "matrix", "payload": {"event_type": "heartbeat"}},
                    separators=(",", ":"),
                ) + "\n\n"
            await asyncio.sleep(1.0)

    headers = {"cache-control": "no-cache", "connection": "keep-alive", "x-accel-buffering": "no"}
    return StreamingResponse(generator(), media_type="text/event-stream", headers=headers)


app.include_router(klusters.router)
app.include_router(missions.router)
app.include_router(docs.router)
app.include_router(artifacts.router)
app.include_router(tasks.router)
app.include_router(mcp.router)
app.include_router(ingestion.router)
app.include_router(search.router)
app.include_router(agents.router)
app.include_router(schema_pack.router)
app.include_router(explorer.router)
app.include_router(onboarding.router)
app.include_router(governance.router)
app.include_router(approvals.router)
app.include_router(ops.router)
app.include_router(slack_integrations.router)
app.include_router(runtime.router)
app.include_router(chat_integrations.router)
app.include_router(feedback.router)
app.include_router(skills.router)
app.include_router(google_chat_integrations.router)
app.include_router(teams_integrations.router)
app.include_router(profiles.router)
app.include_router(evolve.router)
app.include_router(ai.router)
app.include_router(scheduled_jobs.router)
app.include_router(persistence.router)
app.include_router(auth_sessions.router)
app.include_router(oidc_web.router)
app.include_router(remotectl.router)
app.include_router(hooks.router)
app.include_router(hooks.codex_router)


def _web_dir() -> Path | None:
    candidates = [
        Path(__file__).resolve().parent.parent / "web",
        Path(__file__).resolve().parents[2] / "web",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


web_dir = _web_dir()
if web_dir is not None:
    ui_dir = web_dir / "build"
    if not ui_dir.exists():
        ui_dir = web_dir
    app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")
