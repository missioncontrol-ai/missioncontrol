import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import shutil
import threading
import tarfile
import time
import sys
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlencode, urlparse

import paho.mqtt.client as mqtt

DEFAULT_BASE_URL = "http://localhost:8008"
USER_AGENT = "missioncontrol-mcp/0.1.0"
DEFAULT_HTTP_TIMEOUT_SEC = 20.0
DEFAULT_HTTP_RETRIES = 2
DEFAULT_HTTP_RETRY_BACKOFF_MS = 250
DEFAULT_STARTUP_PREFLIGHT = "health"
DEFAULT_HEALTHCHECK_PATH = "/"
DEFAULT_FAIL_OPEN_ON_LIST = False
DEFAULT_MCP_MODE = "direct"
DEFAULT_DAEMON_HOST = "127.0.0.1"
DEFAULT_DAEMON_PORT = 8765
DEFAULT_DAEMON_CONNECT_TIMEOUT_MS = 100
DEFAULT_DAEMON_TOOLS_TIMEOUT_MS = 2000
DEFAULT_DAEMON_CALL_TIMEOUT_MS = 30000
DEFAULT_TOOLS_CACHE_TTL_SEC = 60
DEFAULT_TOOLS_STALE_SEC = 600
DEFAULT_AUTH_MODE = "auto"

PREFLIGHT_MODES = {"none", "health", "tools"}


class McpProtocolError(Exception):
    pass


class MissionControlHttpError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def log(message: str) -> None:
    sys.stderr.write(f"[missioncontrol-mcp] {message}\n")
    sys.stderr.flush()


def parse_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def parse_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def base_urls() -> list[str]:
    configured = os.getenv("MC_BASE_URLS", "").strip()
    if configured:
        items = [x.strip().rstrip("/") for x in configured.split(",") if x.strip()]
        if items:
            return items
    return [os.getenv("MC_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")]


def build_api_url(base_url: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"{base_url}{path}"


def classify_http_error(exc: Exception) -> str:
    if isinstance(exc, error.HTTPError):
        if exc.code in {401, 403}:
            return "auth_error"
        if 500 <= exc.code <= 599:
            return "http_5xx"
        return "http_4xx"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, error.URLError):
        reason = str(exc.reason).lower()
        if "name or service not known" in reason or "temporary failure in name resolution" in reason:
            return "dns_error"
        if "timed out" in reason:
            return "timeout"
        if "ssl" in reason or "tls" in reason or "certificate" in reason:
            return "tls_error"
        return "network_error"
    return "unknown_error"


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, error.HTTPError):
        return 500 <= exc.code <= 599
    if isinstance(exc, (error.URLError, TimeoutError)):
        return True
    return False


class MissionControlHttpClient:
    def __init__(self) -> None:
        self._base_urls = base_urls()
        self._timeout_sec = parse_float_env("MC_HTTP_TIMEOUT_SEC", DEFAULT_HTTP_TIMEOUT_SEC)
        self._retries = max(0, parse_int_env("MC_HTTP_RETRIES", DEFAULT_HTTP_RETRIES))
        self._retry_backoff_ms = max(0, parse_int_env("MC_HTTP_RETRY_BACKOFF_MS", DEFAULT_HTTP_RETRY_BACKOFF_MS))
        self._last_success_base_url: str | None = None
        self._auth_mode = (os.getenv("MC_AUTH_MODE", DEFAULT_AUTH_MODE) or DEFAULT_AUTH_MODE).strip().lower()
        self._token = os.getenv("MC_TOKEN")
        self._oidc_token_url = os.getenv("MC_OIDC_TOKEN_URL", "").strip()
        self._oidc_client_id = os.getenv("MC_OIDC_CLIENT_ID", "").strip()
        self._oidc_client_secret = os.getenv("MC_OIDC_CLIENT_SECRET", "").strip()
        self._oidc_audience = os.getenv("MC_OIDC_AUDIENCE", "").strip()
        self._oidc_scope = os.getenv("MC_OIDC_SCOPE", "").strip()
        self._refresh_skew_sec = max(30, parse_int_env("MC_OIDC_REFRESH_SKEW_SEC", 120))
        self._oidc_access_token: str = ""
        self._oidc_token_expires_at: float = 0.0
        self._token_lock = threading.Lock()

    @property
    def preferred_base_url(self) -> str:
        if self._last_success_base_url:
            return self._last_success_base_url
        return self._base_urls[0] if self._base_urls else DEFAULT_BASE_URL

    @property
    def base_url_candidates(self) -> list[str]:
        if self._last_success_base_url and self._last_success_base_url in self._base_urls:
            remaining = [u for u in self._base_urls if u != self._last_success_base_url]
            return [self._last_success_base_url] + remaining
        return list(self._base_urls)

    def _auth_mode_effective(self) -> str:
        if self._auth_mode in {"token", "oidc"}:
            return self._auth_mode
        oidc_ready = bool(self._oidc_token_url and self._oidc_client_id and self._oidc_client_secret)
        if oidc_ready:
            return "oidc"
        return "token"

    def _ensure_oidc_token(self) -> str:
        now = time.time()
        with self._token_lock:
            if self._oidc_access_token and now < max(0.0, self._oidc_token_expires_at - self._refresh_skew_sec):
                return self._oidc_access_token
            if not self._oidc_token_url or not self._oidc_client_id or not self._oidc_client_secret:
                raise MissionControlHttpError("auth_error", "OIDC client credentials are not configured")
            body = {
                "grant_type": "client_credentials",
                "client_id": self._oidc_client_id,
                "client_secret": self._oidc_client_secret,
            }
            if self._oidc_audience:
                body["audience"] = self._oidc_audience
            if self._oidc_scope:
                body["scope"] = self._oidc_scope
            data = urlencode(body).encode("utf-8")
            req = request.Request(
                url=self._oidc_token_url,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            token_timeout = parse_float_env("MC_OIDC_TOKEN_TIMEOUT_SEC", 2.0)
            try:
                with request.urlopen(req, timeout=token_timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    payload = json.loads(raw) if raw else {}
            except Exception as exc:
                raise MissionControlHttpError("auth_error", f"OIDC token request failed: {exc}") from exc
            token = str(payload.get("access_token") or "").strip()
            if not token:
                raise MissionControlHttpError("auth_error", "OIDC token response missing access_token")
            expires_in = int(payload.get("expires_in") or 3600)
            self._oidc_access_token = token
            self._oidc_token_expires_at = now + max(60, expires_in)
            return self._oidc_access_token

    def _authorization_header(self) -> str | None:
        mode = self._auth_mode_effective()
        if mode == "oidc":
            token = self._ensure_oidc_token()
            return f"Bearer {token}"
        token = (self._token or "").strip()
        if token:
            return f"Bearer {token}"
        return None

    def _request_once(self, *, base_url: str, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        url = build_api_url(base_url, path)
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        auth_header = self._authorization_header()
        if auth_header:
            headers["Authorization"] = auth_header
        req = request.Request(url=url, data=data, headers=headers, method=method)
        with request.urlopen(req, timeout=self._timeout_sec) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return {}
            return json.loads(body)

    def http_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        request_id = str(uuid.uuid4())[:8]
        attempts = self._retries + 1
        last_error: Exception | None = None
        last_error_category = "unknown_error"

        for attempt in range(1, attempts + 1):
            for base_url in self.base_url_candidates:
                try:
                    result = self._request_once(
                        base_url=base_url,
                        method=method,
                        path=path,
                        payload=payload,
                    )
                    self._last_success_base_url = base_url
                    return result
                except Exception as exc:  # pragma: no cover - error classification is tested indirectly
                    last_error = exc
                    last_error_category = classify_http_error(exc)
                    if isinstance(exc, error.HTTPError):
                        detail = exc.read().decode("utf-8", errors="replace")
                        message = f"MissionControl API HTTP {exc.code}: {detail}"
                    else:
                        message = f"MissionControl API unreachable: {exc}"
                    log(
                        "request_id="
                        f"{request_id} category={last_error_category} method={method} path={path} "
                        f"base_url={base_url} attempt={attempt}/{attempts} error={message}"
                    )
                    if not _is_retryable(exc):
                        raise MissionControlHttpError(last_error_category, message) from exc

            if attempt < attempts:
                sleep_sec = (self._retry_backoff_ms / 1000.0) * attempt
                time.sleep(sleep_sec)

        if isinstance(last_error, error.HTTPError):
            detail = last_error.read().decode("utf-8", errors="replace")
            message = f"MissionControl API HTTP {last_error.code}: {detail}"
        elif last_error is not None:
            message = f"MissionControl API unreachable: {last_error}"
        else:
            message = "MissionControl API unreachable: unknown error"
        raise MissionControlHttpError(last_error_category, message) from last_error


_HTTP_CLIENT_SINGLETON: MissionControlHttpClient | None = None


def http_json(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    """
    Backward-compatible helper for explorer/admin CLIs.
    Uses a lazily initialized shared client.
    """
    global _HTTP_CLIENT_SINGLETON
    if _HTTP_CLIENT_SINGLETON is None:
        _HTTP_CLIENT_SINGLETON = MissionControlHttpClient()
    return _HTTP_CLIENT_SINGLETON.http_json(method, path, payload)


def read_message() -> dict[str, Any] | None:
    content_length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, sep, value = line.decode("utf-8").partition(":")
        if sep != ":":
            raise McpProtocolError(f"Invalid header line: {line!r}")
        if key.lower().strip() == "content-length":
            content_length = int(value.strip())

    if content_length is None:
        raise McpProtocolError("Missing Content-Length header")

    body = sys.stdin.buffer.read(content_length)
    if len(body) != content_length:
        raise McpProtocolError("Unexpected EOF while reading message body")

    return json.loads(body.decode("utf-8"))


def write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def rpc_result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _preflight_mode() -> str:
    raw = os.getenv("MC_STARTUP_PREFLIGHT", DEFAULT_STARTUP_PREFLIGHT).strip().lower()
    if raw in PREFLIGHT_MODES:
        return raw
    return DEFAULT_STARTUP_PREFLIGHT


def _healthcheck_path() -> str:
    path = os.getenv("MC_HEALTHCHECK_PATH", DEFAULT_HEALTHCHECK_PATH).strip()
    if not path:
        return DEFAULT_HEALTHCHECK_PATH
    if not path.startswith("/"):
        path = "/" + path
    return path


def _preflight(http: MissionControlHttpClient, mode: str) -> None:
    if mode == "none":
        return
    if mode == "health":
        http.http_json("GET", _healthcheck_path())
        return
    if mode == "tools":
        http.http_json("GET", "/mcp/tools")
        return


def _start_preflight_async(
    *,
    http: MissionControlHttpClient,
    preflight_mode: str,
    preflight_state: dict[str, bool],
) -> None:
    if preflight_state.get("done"):
        return
    preflight_state["done"] = True

    def _runner() -> None:
        try:
            _preflight(http, preflight_mode)
        except Exception as exc:
            log(f"preflight_failed mode={preflight_mode} base_url={http.preferred_base_url} error={exc}")

    t = threading.Thread(target=_runner, daemon=True, name="missioncontrol-mcp-preflight")
    t.start()


def _start_profile_sync_on_init(
    *,
    profile_name: str,
    profile_sync_manager: "ProfileSyncManager",
    timeout_sec: int,
) -> None:
    def _runner() -> None:
        result_holder: list[Any] = []
        exc_holder: list[Exception] = []

        def _do_sync() -> None:
            try:
                result_holder.append(profile_sync_manager.sync(profile_name))
            except Exception as exc:
                exc_holder.append(exc)

        worker = threading.Thread(target=_do_sync, daemon=True, name="missioncontrol-profile-sync")
        worker.start()
        worker.join(timeout=float(timeout_sec))
        if worker.is_alive():
            log(f"Profile sync still running after {timeout_sec}s for '{profile_name}'")
            return
        if exc_holder:
            log(f"Profile sync skipped: {exc_holder[0]}")
            return
        if result_holder and result_holder[0].get("changed"):
            log(f"Profile '{profile_name}' synced")

    t = threading.Thread(target=_runner, daemon=True, name="missioncontrol-profile-sync-init")
    t.start()


def handle_tools_list(msg_id: Any, http: MissionControlHttpClient, fail_open_on_list: bool) -> dict[str, Any]:
    try:
        tools = http.http_json("GET", "/mcp/tools")
    except MissionControlHttpError as exc:
        if fail_open_on_list:
            warning = {
                "warning": str(exc),
                "category": exc.category,
                "base_url": http.preferred_base_url,
            }
            profile_tools = [
                {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
                for t in _PROFILE_LOCAL_TOOLS
            ]
            return rpc_result(msg_id, {"tools": profile_tools, "_missioncontrol_warning": warning})
        raise
    mapped = []
    for tool in tools:
        mapped.append(
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "inputSchema": tool.get("input_schema", {"type": "object", "properties": {}}),
            }
        )
    # Inject local profile tools
    for t in _PROFILE_LOCAL_TOOLS:
        mapped.append({"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]})
    return rpc_result(msg_id, {"tools": mapped})


def handle_tools_call(msg_id: Any, params: dict[str, Any], http: MissionControlHttpClient) -> dict[str, Any]:
    tool_name = params.get("name")
    tool_args = params.get("arguments") or {}
    if not tool_name:
        return rpc_error(msg_id, -32602, "Missing tool name")

    api_result = http.http_json("POST", "/mcp/call", {"tool": tool_name, "args": tool_args})
    ok = bool(api_result.get("ok"))
    if not ok:
        err = api_result.get("error") or "Tool call failed"
        return rpc_result(
            msg_id,
            {
                "content": [{"type": "text", "text": str(err)}],
                "isError": True,
            },
        )

    return rpc_result(
        msg_id,
        {
            "content": [{"type": "text", "text": json.dumps(api_result.get("result", {}))}],
            "isError": False,
        },
    )


def _daemon_host() -> str:
    return (os.getenv("MC_DAEMON_HOST", DEFAULT_DAEMON_HOST) or DEFAULT_DAEMON_HOST).strip()


def _daemon_port() -> int:
    return max(1, min(65535, parse_int_env("MC_DAEMON_PORT", DEFAULT_DAEMON_PORT)))


def _daemon_base_url() -> str:
    return f"http://{_daemon_host()}:{_daemon_port()}"


def _daemon_call(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout_ms: int = 2000) -> dict[str, Any]:
    url = f"{_daemon_base_url()}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": USER_AGENT},
        method=method,
    )
    timeout_sec = max(0.05, float(timeout_ms) / 1000.0)
    with request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


class DaemonClient:
    def __init__(self) -> None:
        self._connect_timeout_ms = max(50, parse_int_env("MC_DAEMON_CONNECT_TIMEOUT_MS", DEFAULT_DAEMON_CONNECT_TIMEOUT_MS))
        self._tools_timeout_ms = max(250, parse_int_env("MC_DAEMON_TOOLS_TIMEOUT_MS", DEFAULT_DAEMON_TOOLS_TIMEOUT_MS))
        self._call_timeout_ms = max(1000, parse_int_env("MC_DAEMON_CALL_TIMEOUT_MS", DEFAULT_DAEMON_CALL_TIMEOUT_MS))

    def initialize(self) -> dict[str, Any]:
        return _daemon_call("/v1/initialize", method="POST", payload={}, timeout_ms=self._connect_timeout_ms)

    def tools_list(self) -> dict[str, Any]:
        return _daemon_call("/v1/tools", method="GET", payload=None, timeout_ms=self._tools_timeout_ms)

    def tools_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return _daemon_call(
            "/v1/call",
            method="POST",
            payload={"name": name, "arguments": arguments},
            timeout_ms=self._call_timeout_ms,
        )


class _CircuitBreaker:
    def __init__(
        self,
        *,
        name: str,
        window_sec: int,
        min_requests: int,
        failure_rate_open: float,
        consecutive_failures_open: int,
        half_open_probe_sec: int,
    ) -> None:
        self.name = name
        self.window_sec = max(5, window_sec)
        self.min_requests = max(1, min_requests)
        self.failure_rate_open = max(0.1, min(1.0, failure_rate_open))
        self.consecutive_failures_open = max(1, consecutive_failures_open)
        self.half_open_probe_sec = max(1, half_open_probe_sec)
        self._state = "closed"
        self._events: list[tuple[float, bool]] = []
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._last_probe_at = 0.0
        self._lock = threading.Lock()

    def _trim(self, now: float) -> None:
        cutoff = now - float(self.window_sec)
        self._events = [e for e in self._events if e[0] >= cutoff]

    def allow(self) -> bool:
        now = time.time()
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if now - self._opened_at >= self.half_open_probe_sec:
                    self._state = "half_open"
                    self._last_probe_at = now
                    return True
                return False
            if self._state == "half_open":
                if now - self._last_probe_at >= self.half_open_probe_sec:
                    self._last_probe_at = now
                    return True
                return False
            return True

    def record(self, success: bool) -> None:
        now = time.time()
        with self._lock:
            self._trim(now)
            self._events.append((now, success))
            if success:
                self._consecutive_failures = 0
                if self._state == "half_open":
                    self._state = "closed"
                return

            self._consecutive_failures += 1
            total = len(self._events)
            failures = len([1 for _, ok in self._events if not ok])
            failure_rate = (failures / total) if total else 0.0
            should_open = (
                self._consecutive_failures >= self.consecutive_failures_open
                or (total >= self.min_requests and failure_rate >= self.failure_rate_open)
            )
            if should_open:
                self._state = "open"
                self._opened_at = now

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._trim(now)
            total = len(self._events)
            failures = len([1 for _, ok in self._events if not ok])
            return {
                "name": self.name,
                "state": self._state,
                "window_sec": self.window_sec,
                "requests": total,
                "failures": failures,
                "failure_rate": (failures / total) if total else 0.0,
                "consecutive_failures": self._consecutive_failures,
            }


class DaemonRuntime:
    def __init__(self) -> None:
        self.http = MissionControlHttpClient()
        self._lock = threading.Lock()
        self._preflight_started = False
        self._cache_tools: list[dict[str, Any]] = []
        self._cache_tools_at: float = 0.0
        self._cache_tools_error: str = ""
        self._cache_tools_category: str = ""
        self._tools_ttl_sec = max(1, parse_int_env("MC_TOOLS_CACHE_TTL_SEC", DEFAULT_TOOLS_CACHE_TTL_SEC))
        self._tools_stale_sec = max(self._tools_ttl_sec, parse_int_env("MC_TOOLS_STALE_SEC", DEFAULT_TOOLS_STALE_SEC))
        self._tools_bulkhead_size = max(1, parse_int_env("MC_BULKHEAD_TOOLS_CONCURRENCY", 4))
        self._call_bulkhead_size = max(1, parse_int_env("MC_BULKHEAD_CALL_CONCURRENCY", 8))
        self._tools_sem = threading.BoundedSemaphore(self._tools_bulkhead_size)
        self._call_sem = threading.BoundedSemaphore(self._call_bulkhead_size)
        self._tools_inflight = 0
        self._call_inflight = 0
        self._tools_rejections = 0
        self._call_rejections = 0
        self._breaker_tools = _CircuitBreaker(
            name="tools",
            window_sec=parse_int_env("MC_BREAKER_WINDOW_SEC", 20),
            min_requests=parse_int_env("MC_BREAKER_MIN_REQUESTS", 20),
            failure_rate_open=parse_float_env("MC_BREAKER_FAILURE_RATE_OPEN", 0.5),
            consecutive_failures_open=parse_int_env("MC_BREAKER_CONSECUTIVE_FAILURES_OPEN", 10),
            half_open_probe_sec=parse_int_env("MC_BREAKER_HALF_OPEN_PROBE_SEC", 5),
        )
        self._breaker_call = _CircuitBreaker(
            name="call",
            window_sec=parse_int_env("MC_BREAKER_WINDOW_SEC", 20),
            min_requests=parse_int_env("MC_BREAKER_MIN_REQUESTS", 20),
            failure_rate_open=parse_float_env("MC_BREAKER_FAILURE_RATE_OPEN", 0.5),
            consecutive_failures_open=parse_int_env("MC_BREAKER_CONSECUTIVE_FAILURES_OPEN", 10),
            half_open_probe_sec=parse_int_env("MC_BREAKER_HALF_OPEN_PROBE_SEC", 5),
        )

    def _exec_with_guard(self, *, kind: str, fn):
        if kind == "tools":
            breaker = self._breaker_tools
            sem = self._tools_sem
            inflight_name = "_tools_inflight"
            rejection_name = "_tools_rejections"
        else:
            breaker = self._breaker_call
            sem = self._call_sem
            inflight_name = "_call_inflight"
            rejection_name = "_call_rejections"

        if not breaker.allow():
            raise MissionControlHttpError("circuit_open", f"{kind} circuit breaker is open")
        acquired = sem.acquire(blocking=False)
        if not acquired:
            with self._lock:
                setattr(self, rejection_name, int(getattr(self, rejection_name)) + 1)
            raise MissionControlHttpError("overloaded", f"{kind} bulkhead is saturated")
        with self._lock:
            setattr(self, inflight_name, int(getattr(self, inflight_name)) + 1)
        try:
            out = fn()
            breaker.record(True)
            return out
        except Exception:
            breaker.record(False)
            raise
        finally:
            with self._lock:
                setattr(self, inflight_name, max(0, int(getattr(self, inflight_name)) - 1))
            sem.release()

    def _cache_age(self) -> float:
        if self._cache_tools_at <= 0:
            return float("inf")
        return max(0.0, time.time() - self._cache_tools_at)

    def _refresh_tools(self) -> list[dict[str, Any]]:
        tools = self._exec_with_guard(kind="tools", fn=lambda: self.http.http_json("GET", "/mcp/tools"))
        if not isinstance(tools, list):
            raise MissionControlHttpError("http_4xx", "Invalid /mcp/tools response shape")
        with self._lock:
            self._cache_tools = tools
            self._cache_tools_at = time.time()
            self._cache_tools_error = ""
            self._cache_tools_category = ""
        return tools

    def _start_preflight_async(self) -> None:
        with self._lock:
            if self._preflight_started:
                return
            self._preflight_started = True

        def _runner() -> None:
            try:
                _preflight(self.http, _preflight_mode())
                self._refresh_tools()
            except Exception as exc:
                with self._lock:
                    self._cache_tools_error = str(exc)
                    self._cache_tools_category = classify_http_error(exc) if isinstance(exc, Exception) else "unknown_error"
                log(f"daemon preflight warning: {exc}")

        t = threading.Thread(target=_runner, daemon=True, name="missioncontrol-mcpd-preflight")
        t.start()

    def initialize(self) -> dict[str, Any]:
        self._start_preflight_async()
        age = self._cache_age()
        return {"ok": True, "preflight_started": True, "cache_age_sec": None if age == float("inf") else age}

    def tools_list(self) -> dict[str, Any]:
        age = self._cache_age()
        if age <= self._tools_ttl_sec and self._cache_tools:
            return {"ok": True, "tools": self._cache_tools, "cache": {"state": "fresh", "age_sec": age}}
        try:
            tools = self._refresh_tools()
            age = self._cache_age()
            return {"ok": True, "tools": tools, "cache": {"state": "fresh", "age_sec": age}}
        except Exception as exc:
            age = self._cache_age()
            if self._cache_tools and age <= self._tools_stale_sec:
                warning = {
                    "message": str(exc),
                    "category": classify_http_error(exc),
                    "age_sec": age,
                    "state": "stale",
                }
                return {"ok": True, "tools": self._cache_tools, "cache": warning}
            raise

    def tools_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._exec_with_guard(
            kind="call",
            fn=lambda: self.http.http_json("POST", "/mcp/call", {"tool": name, "args": arguments or {}}),
        )

    def health(self) -> dict[str, Any]:
        age = self._cache_age()
        with self._lock:
            return {
                "ok": True,
                "mode": "daemon",
                "preferred_base_url": self.http.preferred_base_url,
                "cache_tools_age_sec": None if age == float("inf") else age,
                "cache_tools_count": len(self._cache_tools),
                "cache_tools_error": self._cache_tools_error,
                "cache_tools_error_category": self._cache_tools_category,
                "bulkheads": {
                    "tools": {
                        "size": self._tools_bulkhead_size,
                        "inflight": self._tools_inflight,
                        "rejections": self._tools_rejections,
                    },
                    "call": {
                        "size": self._call_bulkhead_size,
                        "inflight": self._call_inflight,
                        "rejections": self._call_rejections,
                    },
                },
                "breakers": {
                    "tools": self._breaker_tools.snapshot(),
                    "call": self._breaker_call.snapshot(),
                },
            }


class _DaemonHandler(BaseHTTPRequestHandler):
    runtime: DaemonRuntime | None = None

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        rt = self.runtime
        if rt is None:
            self._send_json(500, {"ok": False, "error": "runtime_uninitialized"})
            return
        try:
            if self.path in {"/healthz", "/readyz", "/livez", "/v1/health"}:
                self._send_json(200, rt.health())
                return
            if self.path == "/v1/tools":
                self._send_json(200, rt.tools_list())
                return
            self._send_json(404, {"ok": False, "error": "not_found"})
        except Exception as exc:
            self._send_json(502, {"ok": False, "error": str(exc), "category": classify_http_error(exc)})

    def do_POST(self) -> None:  # noqa: N802
        rt = self.runtime
        if rt is None:
            self._send_json(500, {"ok": False, "error": "runtime_uninitialized"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {}
        try:
            if self.path == "/v1/initialize":
                self._send_json(200, rt.initialize())
                return
            if self.path == "/v1/call":
                name = str(payload.get("name") or "")
                arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
                if not name:
                    self._send_json(400, {"ok": False, "error": "missing_tool_name"})
                    return
                self._send_json(200, rt.tools_call(name, arguments))
                return
            self._send_json(404, {"ok": False, "error": "not_found"})
        except Exception as exc:
            self._send_json(502, {"ok": False, "error": str(exc), "category": classify_http_error(exc)})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def run_daemon() -> None:
    host = _daemon_host()
    port = _daemon_port()
    runtime = DaemonRuntime()
    _DaemonHandler.runtime = runtime
    server = ThreadingHTTPServer((host, port), _DaemonHandler)
    log(f"starting daemon on {host}:{port} backend={runtime.http.preferred_base_url}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def handle_request(
    message: dict[str, Any],
    *,
    http: MissionControlHttpClient,
    daemon: DaemonClient | None,
    preflight_state: dict[str, bool],
    fail_open_on_list: bool,
    profile_store: "ProfileStore | None" = None,
    profile_sync_manager: "ProfileSyncManager | None" = None,
) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method == "notifications/initialized":
        return None

    if method == "initialize":
        if daemon is not None:
            try:
                daemon.initialize()
            except Exception as exc:
                log(f"daemon initialize warning: {exc}")
        else:
            _start_preflight_async(
                http=http,
                preflight_mode=_preflight_mode(),
                preflight_state=preflight_state,
            )

        # Auto-sync active profile on initialize (fail-graceful)
        if profile_store is not None and profile_sync_manager is not None:
            if parse_bool_env("MC_PROFILE_SYNC_ON_INIT", True):
                profile_name = _resolve_profile_name(profile_store)
                if profile_name:
                    timeout_sec = max(1, parse_int_env("MC_PROFILE_SYNC_TIMEOUT_SEC", 10))
                    _start_profile_sync_on_init(
                        profile_name=profile_name,
                        profile_sync_manager=profile_sync_manager,
                        timeout_sec=timeout_sec,
                    )

        return rpc_result(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {
                        "listChanged": False,
                    }
                },
                "serverInfo": {
                    "name": "missioncontrol-mcp",
                    "version": "0.1.0",
                },
            },
        )

    if method == "ping":
        return rpc_result(msg_id, {})

    if method == "tools/list":
        if daemon is not None:
            try:
                out = daemon.tools_list()
            except Exception as exc:
                if fail_open_on_list:
                    warning = {
                        "warning": str(exc),
                        "category": classify_http_error(exc),
                        "base_url": _daemon_base_url(),
                    }
                    return rpc_result(msg_id, {"tools": [], "_missioncontrol_warning": warning})
                raise
            tools = out.get("tools") if isinstance(out, dict) else []
            mapped = []
            for tool in tools if isinstance(tools, list) else []:
                mapped.append(
                    {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "inputSchema": tool.get("input_schema", {"type": "object", "properties": {}}),
                    }
                )
            result_payload: dict[str, Any] = {"tools": mapped}
            cache_meta = out.get("cache") if isinstance(out, dict) else None
            if isinstance(cache_meta, dict) and cache_meta.get("state") == "stale":
                result_payload["_missioncontrol_warning"] = cache_meta
            # Inject local profile tools
            for t in _PROFILE_LOCAL_TOOLS:
                result_payload["tools"].append(
                    {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
                )
            return rpc_result(msg_id, result_payload)
        return handle_tools_list(msg_id, http, fail_open_on_list)

    if method == "tools/call":
        tool_name = params.get("name") or ""
        tool_args = params.get("arguments") or {}

        # Intercept local profile tools before forwarding to backend
        if tool_name in _PROFILE_TOOL_NAMES and profile_store is not None and profile_sync_manager is not None:
            return _handle_profile_tool(
                msg_id,
                str(tool_name),
                tool_args if isinstance(tool_args, dict) else {},
                http,
                profile_store,
                profile_sync_manager,
            )

        if daemon is not None:
            if not tool_name:
                return rpc_error(msg_id, -32602, "Missing tool name")
            api_result = daemon.tools_call(str(tool_name), tool_args if isinstance(tool_args, dict) else {})
            ok = bool(api_result.get("ok"))
            if not ok:
                err = api_result.get("error") or "Tool call failed"
                return rpc_result(
                    msg_id,
                    {
                        "content": [{"type": "text", "text": str(err)}],
                        "isError": True,
                    },
                )
            return rpc_result(
                msg_id,
                {
                    "content": [{"type": "text", "text": json.dumps(api_result.get("result", {}))}],
                    "isError": False,
                },
            )
        return handle_tools_call(msg_id, params, http)

    if msg_id is None:
        return None

    return rpc_error(msg_id, -32601, f"Method not found: {method}")


def start_mqtt_listener() -> mqtt.Client | None:
    host = os.getenv("MQTT_HOST")
    agent_id = os.getenv("MC_AGENT_ID")
    if not host or not agent_id:
        return None

    port_raw = os.getenv("MQTT_PORT", "1883")
    try:
        port = int(port_raw)
    except ValueError:
        port = 1883

    client = mqtt.Client(client_id=os.getenv("MQTT_CLIENT_ID", f"missioncontrol-mcp-{agent_id}"))
    username = os.getenv("MQTT_USERNAME")
    password = os.getenv("MQTT_PASSWORD")
    if username:
        client.username_pw_set(username, password)

    def on_connect(client, userdata, flags, rc):
        if rc != 0:
            log(f"mqtt connect failed: rc={rc}")
            return
        topic = f"agents/{agent_id}/inbox"
        client.subscribe(topic, qos=1)
        log(f"mqtt subscribed: {topic}")

    def on_message(client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
            log(f"mqtt message on {msg.topic}: {payload}")
        except Exception as exc:
            log(f"mqtt message error: {exc}")

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, keepalive=60)
    client.loop_start()
    return client


def run() -> None:
    mode = (os.getenv("MC_MCP_MODE", DEFAULT_MCP_MODE) or DEFAULT_MCP_MODE).strip().lower()
    if mode == "daemon":
        run_daemon()
        return
    http = MissionControlHttpClient()
    daemon = DaemonClient() if mode == "shim" else None
    fail_open_on_list = parse_bool_env("MC_FAIL_OPEN_ON_LIST", DEFAULT_FAIL_OPEN_ON_LIST)
    preflight_state = {"done": False}
    profile_store = ProfileStore()
    profile_sync_manager = ProfileSyncManager(profile_store, http)
    if daemon is not None:
        log(f"starting shim bridge daemon={_daemon_base_url()} backend={http.preferred_base_url}")
    else:
        log(
            f"starting bridge for {http.preferred_base_url} "
            f"(fallbacks={','.join(http.base_url_candidates)})"
        )
    mqtt_client = start_mqtt_listener()
    while True:
        try:
            message = read_message()
            if message is None:
                break
            response = handle_request(
                message,
                http=http,
                daemon=daemon,
                preflight_state=preflight_state,
                fail_open_on_list=fail_open_on_list,
                profile_store=profile_store,
                profile_sync_manager=profile_sync_manager,
            )
            if response is not None:
                write_message(response)
        except McpProtocolError as exc:
            log(f"protocol error: {exc}")
            break
        except Exception as exc:  # pragma: no cover
            msg_id = None
            if isinstance(locals().get("message"), dict):
                msg_id = locals()["message"].get("id")
            if msg_id is not None:
                write_message(rpc_error(msg_id, -32000, str(exc)))
            else:
                log(f"fatal error: {exc}")
                break
    if mqtt_client is not None:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


def doctor() -> int:
    http = MissionControlHttpClient()
    auth_mode_effective = http._auth_mode_effective()
    findings: dict[str, Any] = {
        "base_url_candidates": http.base_url_candidates,
        "preferred_base_url": http.preferred_base_url,
        "mcp_mode": (os.getenv("MC_MCP_MODE", DEFAULT_MCP_MODE) or DEFAULT_MCP_MODE).strip().lower(),
        "daemon_base_url": _daemon_base_url(),
        "auth_mode_effective": auth_mode_effective,
        "token_present": bool(os.getenv("MC_TOKEN")),
        "oidc_configured": bool(os.getenv("MC_OIDC_TOKEN_URL") and os.getenv("MC_OIDC_CLIENT_ID")),
        "http_timeout_sec": parse_float_env("MC_HTTP_TIMEOUT_SEC", DEFAULT_HTTP_TIMEOUT_SEC),
        "http_retries": parse_int_env("MC_HTTP_RETRIES", DEFAULT_HTTP_RETRIES),
        "http_retry_backoff_ms": parse_int_env("MC_HTTP_RETRY_BACKOFF_MS", DEFAULT_HTTP_RETRY_BACKOFF_MS),
        "startup_preflight": _preflight_mode(),
        "checks": {},
    }

    for base_url in http.base_url_candidates:
        parsed = urlparse(base_url)
        host = parsed.hostname
        if not host:
            findings["checks"][base_url] = {"ok": False, "error": "invalid_url", "error_category": "config_error"}
            continue
        try:
            http._request_once(base_url=base_url, method="GET", path=_healthcheck_path(), payload=None)
            health_ok = True
            health_error = None
            health_category = None
        except Exception as exc:
            health_ok = False
            health_category = classify_http_error(exc)
            health_error = str(exc)

        try:
            tools = http._request_once(base_url=base_url, method="GET", path="/mcp/tools", payload=None)
            tools_ok = True
            tools_count = len(tools) if isinstance(tools, list) else 0
            tools_error = None
            tools_category = None
        except Exception as exc:
            tools_ok = False
            tools_count = 0
            tools_category = classify_http_error(exc)
            tools_error = str(exc)

        findings["checks"][base_url] = {
            "ok": health_ok and tools_ok,
            "health_ok": health_ok,
            "health_error": health_error,
            "health_error_category": health_category,
            "tools_ok": tools_ok,
            "tools_count": tools_count,
            "tools_error": tools_error,
            "tools_error_category": tools_category,
        }

    print(json.dumps(findings, indent=2, sort_keys=True))
    return 0


def _mc_home() -> Path:
    root = Path(os.getenv("MC_HOME", "~/.missioncontrol")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _skills_home() -> Path:
    root = Path(os.getenv("MC_SKILLS_HOME", "~/.missioncontrol/skills")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


# ──────────────────────────────────────────────────────────────
# Profile local state management
# ──────────────────────────────────────────────────────────────

class ProfileStore:
    """Manages ~/.missioncontrol/state.json and profile directories."""

    def __init__(self) -> None:
        self._home = _mc_home()
        self._state_file = self._home / "state.json"
        self._profiles_dir = self._home / "profiles"

    def _load_state(self) -> dict[str, Any]:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text("utf-8"))
            except Exception:
                return {}
        return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self._home.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(state, indent=2), "utf-8")

    def get_last_profile(self) -> str | None:
        return self._load_state().get("last_profile") or None

    def set_last_profile(self, name: str) -> None:
        state = self._load_state()
        state["last_profile"] = name
        self._save_state(state)

    def get_profile_meta(self, name: str) -> dict[str, Any]:
        return self._load_state().get("profiles", {}).get(name, {})

    def set_profile_meta(self, name: str, sha256: str, last_sync_at: str) -> None:
        state = self._load_state()
        state.setdefault("profiles", {})[name] = {
            "sha256": sha256,
            "last_sync_at": last_sync_at,
        }
        self._save_state(state)

    def profile_dir(self, name: str) -> Path:
        return self._profiles_dir / name

    def active_link(self) -> Path:
        return self._profiles_dir / "active"

    def resolve_active_symlink_name(self) -> str | None:
        link = self.active_link()
        if link.is_symlink():
            try:
                target = os.readlink(str(link))
                return Path(target).name
            except Exception:
                return None
        return None


class ProfileSyncManager:
    """Downloads and extracts profile bundles, manages the active symlink."""

    def __init__(self, store: ProfileStore, http: MissionControlHttpClient) -> None:
        self._store = store
        self._http = http

    def sync(self, name: str, force: bool = False) -> dict[str, Any]:
        downloaded = self._http.http_json("GET", f"/me/profiles/{name}/download")
        remote_sha = str(downloaded.get("sha256") or "")

        if not force:
            local_meta = self._store.get_profile_meta(name)
            local_sha = local_meta.get("sha256", "")
            if local_sha and local_sha == remote_sha:
                return {"ok": True, "changed": False, "sha256": remote_sha}

        tarball_b64 = str(downloaded.get("tarball_b64") or "")
        target_dir = self._store.profile_dir(name)
        tmp_dir = self._store.profile_dir(f".tmp-{name}-{os.getpid()}")

        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        _extract_snapshot_to_dir(tarball_b64, tmp_dir)

        if target_dir.exists():
            shutil.rmtree(target_dir)
        tmp_dir.rename(target_dir)

        self.update_symlink(name)

        now = datetime.utcnow().isoformat()
        self._store.set_profile_meta(name, remote_sha, now)
        self._store.set_last_profile(name)

        return {"ok": True, "changed": True, "sha256": remote_sha}

    def update_symlink(self, name: str) -> None:
        active = self._store.active_link()
        active.parent.mkdir(parents=True, exist_ok=True)
        tmp = active.parent / f".active-tmp-{os.getpid()}"
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        os.symlink(name, str(tmp))
        os.replace(str(tmp), str(active))


def _resolve_profile_name(store: ProfileStore) -> str | None:
    """Priority: MC_PROFILE env var → state.json last_profile."""
    env = os.getenv("MC_PROFILE", "").strip()
    if env:
        return env
    return store.get_last_profile()


# Profile MCP tools exposed locally (not forwarded to backend)
_PROFILE_LOCAL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_profiles",
        "description": "List your personal profiles in MissionControl",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "switch_profile",
        "description": "Switch to a named profile (syncs bundle and updates active symlink)",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Profile name to switch to"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "sync_profile",
        "description": "Resync the current active profile bundle",
        "input_schema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "description": "Force re-download even if sha256 matches"},
            },
        },
    },
]

_PROFILE_TOOL_NAMES = {t["name"] for t in _PROFILE_LOCAL_TOOLS}


def _handle_profile_tool(
    msg_id: Any,
    tool_name: str,
    tool_args: dict[str, Any],
    http: MissionControlHttpClient,
    store: ProfileStore,
    sync_mgr: ProfileSyncManager,
) -> dict[str, Any]:
    try:
        if tool_name == "list_profiles":
            profiles = http.http_json("GET", "/me/profiles")
            return rpc_result(msg_id, {
                "content": [{"type": "text", "text": json.dumps(profiles, indent=2)}],
                "isError": False,
            })

        if tool_name == "switch_profile":
            name = str(tool_args.get("name") or "").strip()
            if not name:
                return rpc_result(msg_id, {"content": [{"type": "text", "text": "name is required"}], "isError": True})
            result = sync_mgr.sync(name)
            return rpc_result(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result)}],
                "isError": False,
            })

        if tool_name == "sync_profile":
            force = bool(tool_args.get("force", False))
            name = _resolve_profile_name(store)
            if not name:
                return rpc_result(msg_id, {"content": [{"type": "text", "text": "No active profile configured"}], "isError": True})
            result = sync_mgr.sync(name, force=force)
            return rpc_result(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result)}],
                "isError": False,
            })

    except Exception as exc:
        return rpc_result(msg_id, {
            "content": [{"type": "text", "text": f"Profile tool error: {exc}"}],
            "isError": True,
        })

    return rpc_error(msg_id, -32601, f"Unknown profile tool: {tool_name}")


def _scope_key(mission_id: str, kluster_id: str) -> str:
    if kluster_id:
        return f"{mission_id}/{kluster_id}"
    return mission_id


def _tree_sha256(root: Path) -> str:
    if not root.exists():
        return ""
    hasher = hashlib.sha256()
    for path in sorted([p for p in root.rglob("*") if p.is_file()]):
        rel = str(path.relative_to(root)).replace("\\", "/")
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(path.read_bytes())
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _extract_snapshot_to_dir(tarball_b64: str, target_dir: Path) -> dict[str, bytes]:
    raw = base64.b64decode(tarball_b64)
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = member.name.replace("\\", "/").strip("/")
            if not name or name.startswith("../") or "/../" in name:
                continue
            handle = tf.extractfile(member)
            if handle is None:
                continue
            data = handle.read()
            files[name] = data
            out = target_dir / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
    return files


def _collect_file_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    out: dict[str, bytes] = {}
    for path in sorted([p for p in root.rglob("*") if p.is_file()]):
        rel = str(path.relative_to(root)).replace("\\", "/")
        out[rel] = path.read_bytes()
    return out


def _bundle_sha256_from_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _extract_bundle_entries_from_path(path: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    with tarfile.open(path, mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = member.name.replace("\\", "/").strip("/")
            if not name or name.startswith("../") or "/../" in name:
                raise SystemExit(f"Invalid tar member path for signing: {member.name}")
            handle = tf.extractfile(member)
            if handle is None:
                continue
            entries[name] = handle.read()
    if not entries:
        raise SystemExit("Bundle tarball has no files")
    return entries


def _load_manifest_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.manifest_json:
        try:
            payload = json.loads(args.manifest_json)
        except Exception as exc:
            raise SystemExit(f"--manifest-json is invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SystemExit("--manifest-json must decode to an object")
        return payload
    if args.manifest_file:
        try:
            payload = json.loads(Path(args.manifest_file).read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"--manifest-file unreadable/invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SystemExit("--manifest-file must contain a JSON object")
        return payload
    return {}


def _normalized_bundle_manifest_for_signing(
    *,
    scope_type: str,
    scope_id: str,
    mission_id: str,
    kluster_id: str,
    manifest_payload: dict[str, Any],
    entries: dict[str, bytes],
) -> dict[str, Any]:
    listed_files = manifest_payload.get("files")
    remove_paths = manifest_payload.get("remove_paths") or []

    listed_map: dict[str, dict[str, Any]] = {}
    if listed_files is None:
        listed_map = {}
    elif isinstance(listed_files, dict):
        listed_map = {str(k): (v if isinstance(v, dict) else {}) for k, v in listed_files.items()}
    elif isinstance(listed_files, list):
        for item in listed_files:
            if not isinstance(item, dict) or "path" not in item:
                raise SystemExit("manifest.files list items must include path")
            listed_map[str(item["path"])] = item
    else:
        raise SystemExit("manifest.files must be a map or list")

    normalized_files: list[dict[str, Any]] = []
    for path in sorted(entries.keys()):
        data = entries[path]
        computed_sha = hashlib.sha256(data).hexdigest()
        listed = listed_map.get(path) or {}
        listed_sha = listed.get("sha256") if isinstance(listed, dict) else None
        if listed_sha and str(listed_sha) != computed_sha:
            raise SystemExit(f"manifest hash mismatch for path: {path}")
        normalized_files.append({"path": path, "sha256": computed_sha, "size": len(data)})

    normalized_remove_paths: list[str] = []
    if remove_paths:
        if not isinstance(remove_paths, list):
            raise SystemExit("manifest.remove_paths must be a list")
        for raw in remove_paths:
            path = str(raw).replace("\\", "/").strip("/")
            if not path or path.startswith("../") or "/../" in path:
                raise SystemExit(f"Invalid remove path in manifest: {raw}")
            normalized_remove_paths.append(path)
        normalized_remove_paths = sorted(set(normalized_remove_paths))

    return {
        "format": "mc-skill-bundle/v1",
        "scope_type": scope_type,
        "scope_id": scope_id,
        "mission_id": mission_id,
        "kluster_id": kluster_id,
        "files": normalized_files,
        "remove_paths": normalized_remove_paths,
    }


def _bundle_signature_payload(manifest: dict[str, Any], tarball_sha256: str, signature_alg: str) -> str:
    payload = {
        "manifest": manifest,
        "signature_alg": signature_alg,
        "tarball_sha256": tarball_sha256,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _sign_bundle_command(args: argparse.Namespace) -> int:
    signature_alg = (args.signature_alg or "hmac-sha256").strip().lower()
    if signature_alg != "hmac-sha256":
        raise SystemExit("--signature-alg must be hmac-sha256")
    if not args.bundle_file:
        raise SystemExit("--bundle-file is required")
    if not args.mission_id:
        raise SystemExit("--mission-id is required")
    scope_type = (args.scope_type or "").strip().lower()
    if scope_type not in {"mission", "kluster"}:
        raise SystemExit("--scope-type must be mission or kluster")
    scope_id = (args.scope_id or "").strip()
    if not scope_id:
        raise SystemExit("--scope-id is required")
    if scope_type == "mission" and scope_id != args.mission_id:
        raise SystemExit("For mission scope, --scope-id must equal --mission-id")
    if scope_type == "kluster" and not args.kluster_id:
        raise SystemExit("--kluster-id is required for kluster scope")
    if scope_type == "kluster" and scope_id != args.kluster_id:
        raise SystemExit("For kluster scope, --scope-id must equal --kluster-id")

    bundle_path = Path(args.bundle_file)
    if not bundle_path.exists():
        raise SystemExit(f"Bundle file not found: {bundle_path}")
    secret = (args.signing_secret or os.getenv("MC_SKILLS_SIGNING_SECRET") or "").strip()
    if not secret:
        raise SystemExit("Signing secret is required (--signing-secret or MC_SKILLS_SIGNING_SECRET)")

    manifest_payload = _load_manifest_payload(args)
    entries = _extract_bundle_entries_from_path(bundle_path)
    manifest = _normalized_bundle_manifest_for_signing(
        scope_type=scope_type,
        scope_id=scope_id,
        mission_id=args.mission_id,
        kluster_id=args.kluster_id or "",
        manifest_payload=manifest_payload,
        entries=entries,
    )
    tarball_sha256 = _bundle_sha256_from_path(bundle_path)
    signing_payload = _bundle_signature_payload(manifest, tarball_sha256, signature_alg)
    signature = hmac.new(secret.encode("utf-8"), signing_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    result = {
        "signature_alg": signature_alg,
        "signing_key_id": args.signing_key_id or "",
        "signature": signature,
        "tarball_sha256": tarball_sha256,
        "manifest": manifest,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _call_mcp_tool(http: MissionControlHttpClient, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    payload = {"tool": tool, "args": args}
    out = http.http_json("POST", "/mcp/call", payload)
    if not out.get("ok"):
        raise MissionControlHttpError("tool_error", out.get("error") or f"{tool} failed")
    return out.get("result") or {}


def _sync_command(args: argparse.Namespace) -> int:
    http = MissionControlHttpClient()
    mission_id = args.mission_id
    kluster_id = args.kluster_id or ""
    agent_id = args.agent_id or ""

    root = _skills_home()
    snapshots_root = root / "snapshots"
    overlays_root = root / "overlays"
    sessions_root = root / "sessions"
    state_root = root / "state"
    for p in (snapshots_root, overlays_root, sessions_root, state_root):
        p.mkdir(parents=True, exist_ok=True)

    scope = _scope_key(mission_id, kluster_id)
    overlay_dir = overlays_root / scope
    overlay_dir.mkdir(parents=True, exist_ok=True)

    degraded_offline = False
    drift_paths: list[str] = []
    result_payload: dict[str, Any] = {}

    try:
        resolved = http.http_json(
            "GET",
            f"/skills/snapshots/resolve?mission_id={mission_id}&kluster_id={kluster_id}",
        )
        snapshot_id = str(resolved["snapshot_id"])
        downloaded = http.http_json("GET", f"/skills/snapshots/{snapshot_id}/download")
        snapshot_dir = snapshots_root / snapshot_id
        tmp_snapshot_dir = snapshots_root / f".tmp-{snapshot_id}-{int(time.time())}"
        if tmp_snapshot_dir.exists():
            shutil.rmtree(tmp_snapshot_dir)
        tmp_snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_files = _extract_snapshot_to_dir(downloaded["tarball_b64"], tmp_snapshot_dir)
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        tmp_snapshot_dir.rename(snapshot_dir)
        snapshot_sha = str(downloaded["sha256"])
        manifest = downloaded.get("manifest") or {}
    except Exception as exc:
        degraded_offline = True
        log(f"sync degraded: could not resolve/download remote snapshot: {exc}")
        existing = sorted([p for p in snapshots_root.glob("*") if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
        if not existing:
            raise SystemExit("No local snapshot available for offline fallback")
        snapshot_dir = existing[0]
        snapshot_id = snapshot_dir.name
        snapshot_files = _collect_file_bytes(snapshot_dir)
        snapshot_sha = _tree_sha256(snapshot_dir)
        manifest = {}

    session_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "-" + str(uuid.uuid4())[:8]
    session_active_dir = sessions_root / session_id / "active"
    session_active_dir.mkdir(parents=True, exist_ok=True)

    for rel, data in snapshot_files.items():
        out = session_active_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)

    overlay_files = _collect_file_bytes(overlay_dir)
    for rel, data in overlay_files.items():
        current = session_active_dir / rel
        if current.exists():
            current_bytes = current.read_bytes()
            if current_bytes != data:
                drift_paths.append(rel)
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(data)

    local_overlay_sha = _tree_sha256(overlay_dir)
    drift_flag = bool(drift_paths)
    drift_details = {"conflicts": drift_paths[:200], "conflict_count": len(drift_paths)}

    try:
        ack_result = http.http_json(
            "POST",
            "/skills/sync/ack",
            {
                "mission_id": mission_id,
                "kluster_id": kluster_id,
                "agent_id": agent_id,
                "snapshot_id": snapshot_id,
                "snapshot_sha256": snapshot_sha,
                "local_overlay_sha256": local_overlay_sha,
                "degraded_offline": degraded_offline,
                "drift_flag": drift_flag,
                "drift_details": drift_details,
            },
        )
    except Exception as exc:
        ack_result = {"warning": f"ack failed: {exc}"}

    result_payload = {
        "ok": True,
        "mission_id": mission_id,
        "kluster_id": kluster_id,
        "snapshot_id": snapshot_id,
        "snapshot_sha256": snapshot_sha,
        "degraded_offline": degraded_offline,
        "drift_flag": drift_flag,
        "drift_details": drift_details,
        "overlay_sha256": local_overlay_sha,
        "session_id": session_id,
        "session_active_dir": str(session_active_dir),
        "overlay_dir": str(overlay_dir),
        "snapshot_dir": str(snapshot_dir),
        "manifest": manifest,
        "ack": ack_result,
    }
    print(json.dumps(result_payload, indent=2, sort_keys=True))
    return 0


def _sync_status_command(args: argparse.Namespace) -> int:
    http = MissionControlHttpClient()
    mission_id = args.mission_id
    kluster_id = args.kluster_id or ""
    agent_id = args.agent_id or ""
    out = http.http_json(
        "GET",
        f"/skills/sync/status?mission_id={mission_id}&kluster_id={kluster_id}&agent_id={agent_id}",
    )
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def _sync_promote_command(args: argparse.Namespace) -> int:
    http = MissionControlHttpClient()
    mission_id = args.mission_id
    kluster_id = args.kluster_id or ""
    agent_id = args.agent_id or ""
    root = _skills_home()
    overlay_dir = root / "overlays" / _scope_key(mission_id, kluster_id)
    overlay_dir.mkdir(parents=True, exist_ok=True)
    local_overlay_sha = _tree_sha256(overlay_dir)
    result = _call_mcp_tool(
        http,
        "promote_local_skill_overlay",
        {
            "mission_id": mission_id,
            "kluster_id": kluster_id,
            "agent_id": agent_id,
            "local_overlay_sha256": local_overlay_sha,
            "note": args.note or "",
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _profile_command(argv: list[str]) -> int:
    """Handles: missioncontrol-mcp profile <subcommand> [args]"""
    p = argparse.ArgumentParser(prog="missioncontrol-mcp profile", description="Manage personal profiles")
    sub = p.add_subparsers(dest="action")

    sub.add_parser("list", help="List profiles")

    use_p = sub.add_parser("use", help="Switch to a profile (sync + update active symlink)")
    use_p.add_argument("name", help="Profile name")

    show_p = sub.add_parser("show", help="Show profile metadata and local sync status")
    show_p.add_argument("name", nargs="?", default="", help="Profile name (default: active)")

    sync_p = sub.add_parser("sync", help="Resync current or named profile")
    sync_p.add_argument("--name", default="", help="Profile name (default: active)")
    sync_p.add_argument("--force", action="store_true", help="Force re-download even if sha256 matches")

    create_p = sub.add_parser("create", help="Create a new (empty) profile record")
    create_p.add_argument("--name", required=True, help="Profile name (slug)")
    create_p.add_argument("--description", default="", help="Description")
    create_p.add_argument("--default", action="store_true", dest="is_default", help="Set as default profile")

    push_p = sub.add_parser("push", help="Tar a local directory and upload as profile bundle")
    push_p.add_argument("--name", required=True, help="Profile name")
    push_p.add_argument("--dir", required=True, help="Directory to bundle")

    pull_p = sub.add_parser("pull", help="Download profile bundle and extract to a local directory")
    pull_p.add_argument("--name", default="", help="Profile name (default: active)")
    pull_p.add_argument("--out-dir", default="", help="Output directory (default: ~/.missioncontrol/profiles/<name>)")

    delete_p = sub.add_parser("delete", help="Delete a profile")
    delete_p.add_argument("name", help="Profile name")

    sub.add_parser("setup-shims", help="Print one-time agent include instructions")

    args = p.parse_args(argv)
    if not args.action:
        p.print_help()
        return 1

    http = MissionControlHttpClient()
    store = ProfileStore()
    sync_mgr = ProfileSyncManager(store, http)

    if args.action == "list":
        profiles = http.http_json("GET", "/me/profiles")
        if not profiles:
            print("No profiles found.")
            return 0
        print(f"{'NAME':<20} {'DEFAULT':<8} {'SHA256':<16} {'LAST SYNC'}")
        for pr in profiles:
            name = pr.get("name", "")
            is_def = "yes" if pr.get("is_default") else "no"
            sha = (pr.get("sha256") or "")[:12]
            meta = store.get_profile_meta(name)
            last_sync = meta.get("last_sync_at", "never")[:19] if meta else "never"
            print(f"{name:<20} {is_def:<8} {sha:<16} {last_sync}")
        return 0

    if args.action == "use":
        result = sync_mgr.sync(args.name)
        if result.get("changed"):
            print(f"Profile '{args.name}' synced and activated (sha256={result.get('sha256', '')[:12]})")
        else:
            print(f"Profile '{args.name}' already up to date — active symlink updated")
            sync_mgr.update_symlink(args.name)
            store.set_last_profile(args.name)
        return 0

    if args.action == "show":
        name = args.name or _resolve_profile_name(store)
        if not name:
            print("No active profile. Use --name or set MC_PROFILE.")
            return 1
        pr = http.http_json("GET", f"/me/profiles/{name}")
        meta = store.get_profile_meta(name)
        active = store.resolve_active_symlink_name()
        print(json.dumps({
            **pr,
            "_local": {
                "active": active == name,
                "last_sync_at": meta.get("last_sync_at") if meta else None,
                "local_sha256": meta.get("sha256") if meta else None,
            },
        }, indent=2))
        return 0

    if args.action == "sync":
        name = args.name or _resolve_profile_name(store)
        if not name:
            print("No active profile. Use --name or set MC_PROFILE.")
            return 1
        result = sync_mgr.sync(name, force=args.force)
        print(json.dumps(result, indent=2))
        return 0

    if args.action == "create":
        # Create with empty tarball (single placeholder file)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"# profile placeholder\n"
            info = tarfile.TarInfo(name="README.md")
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
        tarball_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        result = http.http_json("POST", "/me/profiles", {
            "name": args.name,
            "description": args.description,
            "is_default": args.is_default,
            "manifest": [],
            "tarball_b64": tarball_b64,
        })
        print(json.dumps(result, indent=2))
        return 0

    if args.action == "push":
        src = Path(args.dir).expanduser()
        if not src.is_dir():
            print(f"Directory not found: {src}")
            return 1
        buf = io.BytesIO()
        manifest_files = []
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for fpath in sorted(src.rglob("*")):
                if not fpath.is_file():
                    continue
                rel = str(fpath.relative_to(src)).replace("\\", "/")
                data = fpath.read_bytes()
                info = tarfile.TarInfo(name=rel)
                info.size = len(data)
                info.mtime = int(fpath.stat().st_mtime)
                tf.addfile(info, io.BytesIO(data))
                manifest_files.append({
                    "path": rel,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                })
        tarball_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        # Check if profile exists to decide PUT vs POST
        try:
            existing = http.http_json("GET", f"/me/profiles/{args.name}")
            result = http.http_json("PUT", f"/me/profiles/{args.name}", {
                "name": args.name,
                "description": existing.get("description", ""),
                "is_default": existing.get("is_default", False),
                "manifest": manifest_files,
                "tarball_b64": tarball_b64,
            })
        except MissionControlHttpError:
            result = http.http_json("POST", "/me/profiles", {
                "name": args.name,
                "description": "",
                "is_default": False,
                "manifest": manifest_files,
                "tarball_b64": tarball_b64,
            })
        print(json.dumps(result, indent=2))
        return 0

    if args.action == "pull":
        name = args.name or _resolve_profile_name(store)
        if not name:
            print("No active profile. Use --name or set MC_PROFILE.")
            return 1
        out_dir = Path(args.out_dir).expanduser() if args.out_dir else store.profile_dir(name)
        out_dir.mkdir(parents=True, exist_ok=True)
        dl = http.http_json("GET", f"/me/profiles/{name}/download")
        extracted = _extract_snapshot_to_dir(dl["tarball_b64"], out_dir)
        print(f"Extracted {len(extracted)} file(s) to {out_dir}")
        return 0

    if args.action == "delete":
        http.http_json("DELETE", f"/me/profiles/{args.name}")
        print(f"Profile '{args.name}' deleted.")
        return 0

    if args.action == "setup-shims":
        print("""
Profile shim setup (one-time, per agent):

  Claude Code (~/.claude/CLAUDE.md):
    Add this line:
      @~/.missioncontrol/profiles/active/claude.md

  Codex (~/.codex/config.toml):
    Set:
      instructions_file = "~/.missioncontrol/profiles/active/codex.md"

  OpenClaw (agent config env):
    MC_PROFILE_ACTIVE_PATH=~/.missioncontrol/profiles/active

  Switching profiles:
    missioncontrol-mcp profile use <name>
    (atomic symlink swap — agents pick up new context on next session)
""")
        return 0

    p.print_help()
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="MissionControl MCP bridge")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve", "daemon", "doctor", "sync", "sync-status", "sync-promote", "sign-bundle", "profile"],
        default="serve",
        help="serve MCP over stdio (default), run daemon, doctor checks, sync helpers, bundle signing, or profile management",
    )
    parser.add_argument("--mission-id", default="", help="mission id for sync commands")
    parser.add_argument("--kluster-id", default="", help="optional kluster id for sync commands")
    parser.add_argument("--agent-id", default="", help="optional agent id for sync commands")
    parser.add_argument("--note", default="", help="optional note for sync-promote")
    parser.add_argument("--bundle-file", default="", help="path to skill bundle tar.gz file for sign-bundle")
    parser.add_argument("--scope-type", default="", help="bundle scope type: mission|kluster")
    parser.add_argument("--scope-id", default="", help="bundle scope id (mission_id or kluster_id)")
    parser.add_argument("--signing-key-id", default="", help="optional signing key id metadata")
    parser.add_argument("--signature-alg", default="hmac-sha256", help="signature algorithm (hmac-sha256)")
    parser.add_argument("--manifest-file", default="", help="optional path to raw manifest JSON")
    parser.add_argument("--manifest-json", default="", help="optional raw manifest JSON string")
    parser.add_argument("--signing-secret", default="", help="optional signing secret override")
    args = parser.parse_args(sys.argv[1:2])  # only parse the command; subcommands handle the rest
    if args.command == "daemon":
        os.environ["MC_MCP_MODE"] = "daemon"
        run_daemon()
        return
    if args.command == "doctor":
        raise SystemExit(doctor())
    if args.command == "profile":
        raise SystemExit(_profile_command(sys.argv[2:]))
    # For remaining commands, re-parse with full args
    args = parser.parse_args()
    if args.command == "sync":
        if not args.mission_id:
            raise SystemExit("--mission-id is required for sync")
        raise SystemExit(_sync_command(args))
    if args.command == "sync-status":
        if not args.mission_id:
            raise SystemExit("--mission-id is required for sync-status")
        raise SystemExit(_sync_status_command(args))
    if args.command == "sync-promote":
        if not args.mission_id:
            raise SystemExit("--mission-id is required for sync-promote")
        raise SystemExit(_sync_promote_command(args))
    if args.command == "sign-bundle":
        raise SystemExit(_sign_bundle_command(args))
    run()


if __name__ == "__main__":
    main()
