import hmac
import json
import os
import time
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from urllib import request as urllib_request

from app.services.slack import verify_slack_signature


@dataclass(frozen=True)
class ChatProvider:
    name: str

    def verify(self, *, headers: Mapping[str, str], body: bytes) -> tuple[bool, str]:
        raise NotImplementedError

    def send_event_notification(
        self,
        *,
        channel_id: str,
        workspace_external_id: str,
        channel_metadata: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str]:
        raise NotImplementedError


class SlackProvider(ChatProvider):
    def __init__(self):
        super().__init__(name="slack")

    def verify(self, *, headers: Mapping[str, str], body: bytes) -> tuple[bool, str]:
        return verify_slack_signature(headers=headers, body=body)

    def response(
        self,
        *,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        in_channel: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "response_type": "in_channel" if in_channel else "ephemeral",
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks
        return payload

    def approval_request_blocks(self, *, approval_id: int, mission_id: str, action: str, reason: str) -> list[dict[str, Any]]:
        reason_suffix = f"\nReason: {reason}" if reason else ""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Approval Request #{approval_id}*\n"
                        f"Mission: `{mission_id}`\n"
                        f"Action: `{action}`{reason_suffix}"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "mc_approve",
                        "value": str(approval_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": "mc_reject",
                        "value": str(approval_id),
                    },
                ],
            },
        ]

    def task_created_blocks(self, *, task_id: str | int, kluster_id: str, overlaps: int) -> list[dict[str, Any]]:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Created task *#{task_id}* in `{kluster_id}`."
                        f" Overlap suggestions: *{overlaps}*."
                    ),
                },
            }
        ]

    def search_blocks(self, *, summary_rows: list[str]) -> list[dict[str, Any]]:
        body = "\n".join(f"• {row}" for row in summary_rows)
        return [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Search results*\n{body}"},
            }
        ]

    def outbound_event_blocks(self, *, event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        title = event_type.replace(".", " ").title()
        mission_id = str(payload.get("mission_id") or "")
        lines = [f"*{title}*", f"Mission: `{mission_id}`" if mission_id else "Mission: `n/a`"]
        for key in ("action", "task_id", "approval_request_id", "kluster_id", "channel"):
            value = payload.get(key)
            if value:
                lines.append(f"{key}: `{value}`")
        lines.append(f"time: `{datetime.utcnow().isoformat()}Z`")
        return [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}]

    def send_message(
        self,
        *,
        channel_id: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, str]:
        token = (os.getenv("SLACK_BOT_TOKEN") or "").strip()
        if not token:
            return False, "slack_bot_token_missing"
        body = {
            "channel": channel_id,
            "text": text,
        }
        if blocks:
            body["blocks"] = blocks
        req = urllib_request.Request(
            url="https://slack.com/api/chat.postMessage",
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=3) as resp:
                raw = resp.read().decode("utf-8")
            parsed = json.loads(raw or "{}")
            if not parsed.get("ok"):
                return False, str(parsed.get("error") or "slack_api_error")
            return True, "ok"
        except Exception as exc:
            return False, str(exc)

    def send_event_notification(
        self,
        *,
        channel_id: str,
        workspace_external_id: str,
        channel_metadata: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str]:
        del workspace_external_id
        del channel_metadata
        text = f"[{event_type}] mission={payload.get('mission_id') or 'n/a'}"
        blocks = self.outbound_event_blocks(event_type=event_type, payload=payload)
        return self.send_message(channel_id=channel_id, text=text, blocks=blocks)


class GoogleChatProvider(ChatProvider):
    def __init__(self):
        super().__init__(name="google_chat")

    def verify(self, *, headers: Mapping[str, str], body: bytes) -> tuple[bool, str]:
        signing_secret = (os.getenv("GOOGLE_CHAT_SIGNING_SECRET") or "").strip()
        if signing_secret:
            ok, reason = _verify_hmac_webhook(
                headers=headers,
                body=body,
                signing_secret=signing_secret,
                timestamp_header="x-mc-timestamp",
                signature_header="x-mc-signature",
            )
            return ok, reason
        expected = (os.getenv("GOOGLE_CHAT_VERIFICATION_TOKEN") or "").strip()
        if not expected:
            return False, "google_chat_verification_token_missing"
        header_value = _header(headers, "x-goog-chat-token")
        if header_value:
            ok = hmac.compare_digest(header_value, expected)
            return (ok, "ok" if ok else "google_chat_token_invalid")
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            return False, "google_chat_payload_invalid"
        token = str(payload.get("token") or "").strip()
        if not token:
            return False, "google_chat_token_missing"
        ok = hmac.compare_digest(token, expected)
        return (ok, "ok" if ok else "google_chat_token_invalid")

    def send_event_notification(
        self,
        *,
        channel_id: str,
        workspace_external_id: str,
        channel_metadata: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str]:
        del channel_id
        del workspace_external_id
        webhook_url = str(channel_metadata.get("webhook_url") or "").strip()
        if not webhook_url:
            return False, "google_chat_webhook_url_missing"
        title = event_type.replace(".", " ").title()
        mission_id = str(payload.get("mission_id") or "n/a")
        body: dict[str, Any] = {
            "text": f"{title}: mission={mission_id}",
            "cardsV2": [
                {
                    "cardId": "missioncontrol-event",
                    "card": {
                        "header": {
                            "title": title,
                            "subtitle": f"Mission {mission_id}",
                        },
                        "sections": [
                            {
                                "widgets": [
                                    {
                                        "textParagraph": {
                                            "text": json.dumps(payload, separators=(",", ":")),
                                        }
                                    }
                                ]
                            }
                        ],
                    },
                }
            ],
        }
        req = urllib_request.Request(
            url=webhook_url,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=3):
                pass
            return True, "ok"
        except Exception as exc:
            return False, str(exc)


class TeamsProvider(ChatProvider):
    def __init__(self):
        super().__init__(name="teams")

    def verify(self, *, headers: Mapping[str, str], body: bytes) -> tuple[bool, str]:
        signing_secret = (os.getenv("TEAMS_SIGNING_SECRET") or "").strip()
        if signing_secret:
            ok, reason = _verify_hmac_webhook(
                headers=headers,
                body=body,
                signing_secret=signing_secret,
                timestamp_header="x-mc-timestamp",
                signature_header="x-mc-signature",
            )
            return ok, reason
        expected = (os.getenv("TEAMS_VERIFICATION_TOKEN") or "").strip()
        if not expected:
            return False, "teams_verification_token_missing"
        header_value = _header(headers, "x-missioncontrol-teams-token")
        if header_value:
            ok = hmac.compare_digest(header_value, expected)
            return (ok, "ok" if ok else "teams_token_invalid")
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            return False, "teams_payload_invalid"
        token = str(payload.get("token") or "").strip()
        if not token:
            return False, "teams_token_missing"
        ok = hmac.compare_digest(token, expected)
        return (ok, "ok" if ok else "teams_token_invalid")

    def send_event_notification(
        self,
        *,
        channel_id: str,
        workspace_external_id: str,
        channel_metadata: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str]:
        del channel_id
        del workspace_external_id
        webhook_url = str(channel_metadata.get("webhook_url") or "").strip()
        if not webhook_url:
            return False, "teams_webhook_url_missing"
        title = event_type.replace(".", " ").title()
        mission_id = str(payload.get("mission_id") or "n/a")
        body = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": f"{title}: mission={mission_id}",
            "themeColor": "0078D4",
            "title": title,
            "sections": [
                {
                    "facts": [
                        {"name": "mission_id", "value": mission_id},
                        {"name": "event_type", "value": event_type},
                    ],
                    "text": json.dumps(payload, separators=(",", ":")),
                }
            ],
        }
        req = urllib_request.Request(
            url=webhook_url,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=3):
                pass
            return True, "ok"
        except Exception as exc:
            return False, str(exc)


_PROVIDERS: dict[str, ChatProvider] = {
    "slack": SlackProvider(),
    "google_chat": GoogleChatProvider(),
    "teams": TeamsProvider(),
}


def _header(headers: Mapping[str, str], key: str) -> str:
    lower = key.lower()
    for name, value in headers.items():
        if str(name).lower() == lower:
            return str(value)
    return ""


def _signature_tolerance_seconds() -> int:
    raw = (os.getenv("MC_CHAT_SIGNATURE_TOLERANCE_SEC") or "300").strip()
    try:
        return max(60, min(int(raw), 3600))
    except ValueError:
        return 300


def _verify_hmac_webhook(
    *,
    headers: Mapping[str, str],
    body: bytes,
    signing_secret: str,
    timestamp_header: str,
    signature_header: str,
) -> tuple[bool, str]:
    timestamp = _header(headers, timestamp_header)
    signature = _header(headers, signature_header)
    if not timestamp or not signature:
        return False, "signature_headers_missing"
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "signature_timestamp_invalid"
    tolerance = _signature_tolerance_seconds()
    now = int(time.time())
    if abs(now - ts) > tolerance:
        return False, "signature_timestamp_out_of_range"
    expected_base = f"v1:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    expected_sig = "v1=" + hmac.new(signing_secret.encode("utf-8"), expected_base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, signature):
        return False, "signature_invalid"
    return True, "ok"


def get_chat_provider(name: str) -> ChatProvider | None:
    return _PROVIDERS.get((name or "").strip().lower())


def get_slack_provider() -> SlackProvider:
    provider = _PROVIDERS["slack"]
    assert isinstance(provider, SlackProvider)
    return provider


def get_google_chat_provider() -> GoogleChatProvider:
    provider = _PROVIDERS["google_chat"]
    assert isinstance(provider, GoogleChatProvider)
    return provider


def get_teams_provider() -> TeamsProvider:
    provider = _PROVIDERS["teams"]
    assert isinstance(provider, TeamsProvider)
    return provider
