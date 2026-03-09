from pathlib import Path
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from app.auth import OidcValidationError, OidcValidator, load_auth_settings
from app.db import init_db
from app.db import get_session
from app.services.authz import assert_platform_admin
from app.services.log_export import emit_structured_log, recent_logs
from app.services.telemetry import telemetry
from app.services.mqtt import build_mqtt_service
from app.services.schema_pack import load_schema_pack
from app.services.governance import ensure_governance_policy_seed
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
    feedback,
    skills,
    google_chat_integrations,
    teams_integrations,
    chat_integrations,
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
AUTH_EXEMPT_PATHS = {"/", "/api/openapi.json", "/agent-onboarding.json"}
AUTH_EXEMPT_PREFIXES = (
    "/api/docs",
    "/api/redoc",
    "/ui",
)
AUTH_EXEMPT_WEBHOOK_PATHS = {
    "/integrations/slack/events",
    "/integrations/slack/commands",
    "/integrations/slack/interactions",
    "/integrations/google-chat/events",
    "/integrations/teams/events",
}
TOKEN_FALLBACK_PATH_PREFIXES = ("/mcp",)


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


@app.on_event("shutdown")
def on_shutdown():
    mqtt_service = getattr(app.state, "mqtt", None)
    if mqtt_service is not None:
        mqtt_service.disconnect()


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
    token = _extract_bearer_token(auth_header)
    if not token:
        return _unauthorized(
            request,
            "missing_token",
            "Unauthorized: bearer token is required",
            start=start,
        )

    if AUTH_SETTINGS.mode == "token":
        if token != AUTH_SETTINGS.missioncontrol_token:
            return _unauthorized(request, "invalid_token", "Unauthorized: invalid bearer token", start=start)
        request.state.principal = {
            "subject": "service-token",
            "email": None,
            "auth_type": "token",
        }
        response = await call_next(request)
        return _finalize_response(request, response, start)

    if AUTH_SETTINGS.mode == "oidc":
        return await _require_oidc_token(request, token, call_next, start)

    # AUTH_MODE=dual
    token_fallback_allowed = not AUTH_SETTINGS.oidc_required or any(
        path.startswith(prefix) for prefix in TOKEN_FALLBACK_PATH_PREFIXES
    )
    if token_fallback_allowed and AUTH_SETTINGS.token_enabled() and token == AUTH_SETTINGS.missioncontrol_token:
        request.state.principal = {
            "subject": "service-token",
            "email": None,
            "auth_type": "token",
        }
        response = await call_next(request)
        return _finalize_response(request, response, start)

    if OIDC_VALIDATOR is not None:
        try:
            principal = OIDC_VALIDATOR.validate_token(token)
            request.state.principal = {
                "subject": principal.subject,
                "email": principal.email,
                "auth_type": "oidc",
            }
            response = await call_next(request)
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
    response = await call_next(request)
    return _finalize_response(request, response, start)


def _unauthorized(request: Request, reason: str, detail: str, *, start: float) -> JSONResponse:
    response = JSONResponse(status_code=401, content={"detail": detail, "reason": reason})
    return _finalize_response(request, response, start)


def _finalize_response(request: Request, response, start: float):
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


@app.get("/ops/metrics")
def ops_metrics(request: Request):
    assert_platform_admin(request)
    return telemetry.snapshot()


@app.get("/ops/logs")
def ops_logs(request: Request, limit: int = 200):
    assert_platform_admin(request)
    return {"events": recent_logs(limit=limit)}


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
app.include_router(slack_integrations.router)
app.include_router(chat_integrations.router)
app.include_router(feedback.router)
app.include_router(skills.router)
app.include_router(google_chat_integrations.router)
app.include_router(teams_integrations.router)


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
    app.mount("/ui", StaticFiles(directory=str(web_dir), html=True), name="ui")
