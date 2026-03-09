import hashlib
import hmac
import os
import time
from typing import Mapping


def verify_slack_signature(*, headers: Mapping[str, str], body: bytes) -> tuple[bool, str]:
    secret = (os.getenv("SLACK_SIGNING_SECRET") or "").strip()
    if not secret:
        return False, "slack_signing_secret_missing"

    timestamp = _header(headers, "x-slack-request-timestamp")
    signature = _header(headers, "x-slack-signature")
    if not timestamp or not signature:
        return False, "slack_signature_headers_missing"
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "slack_timestamp_invalid"

    tolerance = _signature_tolerance_seconds()
    now = int(time.time())
    if abs(now - ts) > tolerance:
        return False, "slack_timestamp_out_of_range"

    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    expected = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False, "slack_signature_invalid"
    return True, "ok"


def command_mission_id(text: str | None, explicit_mission_id: str | None = None) -> str | None:
    if explicit_mission_id and explicit_mission_id.strip():
        return explicit_mission_id.strip()
    value = (text or "").strip()
    if not value:
        return None
    for token in value.split():
        if token.startswith("mission_id="):
            mission_id = token.split("=", 1)[1].strip()
            return mission_id or None
    return None


def _signature_tolerance_seconds() -> int:
    raw = (os.getenv("SLACK_SIGNATURE_TOLERANCE_SEC") or "300").strip()
    try:
        return max(60, min(int(raw), 3600))
    except ValueError:
        return 300


def _header(headers: Mapping[str, str], key: str) -> str:
    lower = key.lower()
    for name, value in headers.items():
        if str(name).lower() == lower:
            return str(value)
    return ""
