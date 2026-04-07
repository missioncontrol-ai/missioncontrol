"""Runtime fabric endpoints for nodes, jobs, and leases."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from app.db import get_session
from app.models import (
    ExecutionSession,
    JobLease,
    NodeEvent,
    RuntimeJob,
    RuntimeNode,
    RuntimeNodeSpec,
    RuntimeJoinToken,
    UserSession,
)
from app.services.authz import actor_subject_from_request

router = APIRouter(prefix="/runtime", tags=["runtime"])

RUNTIME_RELEASE_VERSION = os.getenv("MC_RUNTIME_RELEASE_VERSION", "0.2.0")
RUNTIME_RELEASE_BASE_URL = os.getenv(
    "MC_RUNTIME_RELEASE_BASE_URL",
    "https://github.com/missioncontrol-ai/missioncontrol/releases/latest/download",
).rstrip("/")
RUNTIME_RELEASE_FILES = {
    "linux": {
        "x86_64": "mc-linux-x86_64",
        "aarch64": "mc-linux-aarch64",
    },
    "darwin": {
        "x86_64": "mc-darwin-x86_64",
        "aarch64": "mc-darwin-aarch64",
    },
    "windows": {
        "x86_64": "mc-windows-x86_64.exe",
    },
}

_execution_ws_clients: dict[str, set[WebSocket]] = {}


class NodeRegister(BaseModel):
    node_name: str
    hostname: str = ""
    trust_tier: str = "untrusted"
    labels: dict[str, str] = Field(default_factory=dict)
    capacity: dict[str, object] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    runtime_version: str = ""
    bootstrap_token: str = ""


class NodeHeartbeat(BaseModel):
    status: str = "online"
    labels: Optional[dict[str, str]] = None
    capacity: Optional[dict[str, object]] = None
    capabilities: Optional[list[str]] = None
    runtime_version: Optional[str] = None


class JoinTokenCreate(BaseModel):
    expires_in_seconds: int = Field(default=3600, ge=60)
    upgrade_channel: str = "stable"
    desired_version: str = ""
    config: dict[str, object] = Field(default_factory=dict)


class JoinTokenRotate(BaseModel):
    expires_in_seconds: Optional[int] = Field(default=None, ge=60)


class JoinTokenPayload(BaseModel):
    id: str
    node_id: Optional[str] = None
    upgrade_channel: str
    desired_version: str
    status: str
    expires_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    rotation_count: int
    created_at: datetime
    updated_at: datetime


class NodeReconcile(BaseModel):
    drain_state: Optional[str] = None
    desired_version: Optional[str] = None
    health_summary: Optional[str] = None


class NodeInstallBundle(BaseModel):
    node_id: str
    node_name: str
    install_script: str
    config: dict[str, object]
    env: dict[str, str]
    service: dict[str, object]
    join_token: str = ""


class JobCreate(BaseModel):
    mission_id: str = ""
    task_id: Optional[int] = None
    runtime_session_id: str = ""
    runtime_class: str = "container"
    image: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = ""
    mounts: list[dict[str, object]] = Field(default_factory=list)
    artifact_rules: dict[str, object] = Field(default_factory=dict)
    timeout_seconds: int = 3600
    restart_policy: str = "never"
    required_capabilities: list[str] = Field(default_factory=list)
    preferred_labels: dict[str, str] = Field(default_factory=dict)


class LeaseCreate(BaseModel):
    node_id: str


class LeaseClaimResult(BaseModel):
    claimed: bool
    lease: Optional[dict] = None
    job: Optional[dict] = None


class LeaseStatus(BaseModel):
    status: str
    heartbeat_at: Optional[datetime] = None
    started_at: Optional[datetime] = None


class LeaseComplete(BaseModel):
    exit_code: int = 0
    error_message: str = ""


class ExecutionSessionCreate(BaseModel):
    lease_id: str
    runtime_class: str = "container"
    pty_requested: bool = False


class LeaseLogChunk(BaseModel):
    stream: str = "stdout"
    content: str


class ExecutionAttachCreate(BaseModel):
    session_id: str
    stream: str = "stdin"
    content: str


def _json_dump(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _runtime_release_file(os_name: str, arch: str) -> str:
    return RUNTIME_RELEASE_FILES.get((os_name or "").strip().lower(), {}).get((arch or "").strip().lower(), "")


def _config_hash(config: dict[str, object]) -> str:
    normalized = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()


def _node_spec_payload(spec: RuntimeNodeSpec) -> dict:
    return {
        "node_id": spec.node_id,
        "config": json.loads(spec.config_json or "{}"),
        "desired_version": spec.desired_version,
        "upgrade_channel": spec.upgrade_channel,
        "drain_state": spec.drain_state,
        "health_summary": spec.health_summary,
        "config_hash": spec.config_hash,
        "last_reconcile_at": spec.last_reconcile_at,
        "created_at": spec.created_at,
        "updated_at": spec.updated_at,
    }


def _join_token_payload(token: RuntimeJoinToken) -> dict:
    return {
        "id": token.id,
        "node_id": token.node_id,
        "upgrade_channel": token.upgrade_channel,
        "desired_version": token.desired_version,
        "status": token.status,
        "expires_at": token.expires_at,
        "used_at": token.used_at,
        "rotation_count": token.rotation_count,
        "config": json.loads(token.config_json or "{}"),
        "created_at": token.created_at,
        "updated_at": token.updated_at,
    }


def _find_join_token(db, token_id: str, subject: str) -> RuntimeJoinToken:
    token = db.get(RuntimeJoinToken, token_id)
    if not token or token.owner_subject != subject:
        raise HTTPException(status_code=404, detail="Join token not found")
    return token


def _validate_join_token(db, bootstrap_token: str, subject: str) -> RuntimeJoinToken:
    if not bootstrap_token:
        raise HTTPException(status_code=400, detail="bootstrap_token is required")
    token_hash = _hash_token(bootstrap_token)
    now = datetime.utcnow()
    token = db.exec(
        select(RuntimeJoinToken)
        .where(RuntimeJoinToken.token_hash == token_hash)
        .where(RuntimeJoinToken.status == "active")
        .where(RuntimeJoinToken.owner_subject == subject)
    ).first()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid bootstrap token")
    if token.expires_at and token.expires_at < now:
        raise HTTPException(status_code=401, detail="Bootstrap token expired")
    if token.used_at is not None:
        raise HTTPException(status_code=401, detail="Bootstrap token already used")
    token.used_at = now
    token.status = "used"
    db.add(token)
    return token


def _render_node_config(node: RuntimeNode, spec: RuntimeNodeSpec, token: RuntimeJoinToken | None = None) -> dict[str, object]:
    config = json.loads(spec.config_json or "{}")
    config.setdefault("node_name", node.node_name)
    config.setdefault("hostname", node.hostname)
    config.setdefault("trust_tier", node.trust_tier)
    config.setdefault("labels", json.loads(node.labels_json or "{}"))
    config.setdefault("capabilities", json.loads(node.capabilities_json or "[]"))
    config.setdefault("upgrade_channel", spec.upgrade_channel)
    config.setdefault("desired_version", spec.desired_version)
    if token:
        config.setdefault("join_token_id", token.id)
        config.setdefault("join_token_channel", token.upgrade_channel)
    return config


def _ensure_node_spec(
    db,
    subject: str,
    node: RuntimeNode,
    token: RuntimeJoinToken | None = None,
    config_override: dict[str, object] | None = None,
) -> RuntimeNodeSpec:
    spec = db.exec(
        select(RuntimeNodeSpec).where(RuntimeNodeSpec.node_id == node.id).where(RuntimeNodeSpec.owner_subject == subject)
    ).first()
    if spec is None:
        config = {
            "node_name": node.node_name,
            "trust_tier": node.trust_tier,
            "labels": json.loads(node.labels_json or "{}"),
        }
        if config_override:
            config.update(config_override)
        desired_version = (token.desired_version if token else node.runtime_version) or "mc-runtime-node"
        upgrade_channel = token.upgrade_channel if token else "stable"
        spec = RuntimeNodeSpec(
            owner_subject=subject,
            node_id=node.id,
            config_json=_json_dump(config),
            desired_version=desired_version,
            upgrade_channel=upgrade_channel,
            config_hash=_config_hash(config),
        )
        db.add(spec)
    return spec


def _node_config_payload(node: RuntimeNode, spec: RuntimeNodeSpec) -> dict:
    return {
        "node_id": node.id,
        "config": _render_node_config(node, spec),
        "spec": _node_spec_payload(spec),
    }


def _node_install_bundle(
    node: RuntimeNode,
    spec: RuntimeNodeSpec,
    base_url: str,
    token: RuntimeJoinToken | None = None,
) -> dict:
    config = _render_node_config(node, spec, token)
    binary_url = f"{base_url}/runtime/releases/latest/download"
    env = {
        "MC_BASE_URL": base_url,
        "MC_NODE_NAME": node.node_name,
        "MC_NODE_HOSTNAME": node.hostname,
        "MC_NODE_TRUST_TIER": node.trust_tier,
        "MC_NODE_UPGRADE_CHANNEL": spec.upgrade_channel,
        "MC_NODE_DESIRED_VERSION": spec.desired_version,
        "MC_NODE_POLL_SECONDS": str(config.get("poll_seconds", 30)),
        "MC_NODE_HEARTBEAT_SECONDS": str(config.get("heartbeat_seconds", 15)),
        "MC_NODE_UPGRADE_MANIFEST_URL": config.get("upgrade_manifest_url", ""),
        "MC_NODE_BINARY_URL": str(config.get("binary_url") or binary_url),
    }
    if token and token.id:
        env["MC_NODE_TOKEN_ID"] = token.id
    if token and token.node_id:
        env["MC_NODE_BOUND_NODE_ID"] = token.node_id
    service = {
        "name": "mc-node.service",
        "unit_path": "/etc/systemd/system/mc-node.service",
        "command": "mc node run",
        "restart": "always",
        "after": ["network-online.target"],
    }
    return {
        "node_id": node.id,
        "node_name": node.node_name,
        "install_script": _node_install_script(base_url, node.id, env),
        "config": config,
        "env": env,
        "service": service,
        "join_token": token.id if token else "",
    }


def _node_install_script(base_url: str, node_id: str, env: dict[str, str]) -> str:
    binary_url = f"{base_url}/runtime/releases/latest/download"
    env_lines = [
        "# MissionControl node settings",
        *[f"{key}={value}" for key, value in env.items() if value is not None and str(value) != ""],
    ]
    env_body = "\n".join(env_lines) + "\n"
    service_body = (
        "[Unit]\n"
        "Description=MissionControl Node Agent\n"
        "Wants=network-online.target\n"
        "After=network-online.target\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "User=root\n"
        "Group=root\n"
        "EnvironmentFile=-/etc/missioncontrol/mc-node.service.env\n"
        "ExecStart=/usr/local/bin/mc node run\n"
        "Restart=always\n"
        "RestartSec=5s\n"
        "KillMode=control-group\n"
        "TimeoutStartSec=0\n"
        "LimitNOFILE=1048576\n"
    )
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        f"mc_bin='{binary_url}'\n"
        "if [ -n \"$mc_bin\" ]; then\n"
        "  install -d /usr/local/bin\n"
        "  curl -fsSL \"$mc_bin\" -o /usr/local/bin/mc\n"
        "  chmod 0755 /usr/local/bin/mc\n"
        "elif ! command -v mc >/dev/null 2>&1; then\n"
        "  echo '[ERROR] mc binary not found and release artifact could not be resolved' >&2\n"
        "  exit 1\n"
        "fi\n"
        "install -d /etc/missioncontrol /etc/systemd/system\n"
        "cat > /etc/missioncontrol/mc-node.service.env <<'EOF'\n"
        f"{env_body}"
        "EOF\n"
        "chmod 0600 /etc/missioncontrol/mc-node.service.env\n"
        "cat > /etc/systemd/system/mc-node.service <<'EOF'\n"
        f"{service_body}"
        "EOF\n"
        "systemctl daemon-reload\n"
        "systemctl enable --now mc-node.service\n"
    )


def _mutate_node_spec_state(node_id: str, request: Request, drain_state: str):
    subject = actor_subject_from_request(request)
    now = datetime.utcnow()
    with get_session() as db:
        node = db.get(RuntimeNode, node_id)
        if not node or node.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Node not found")
        spec = db.exec(
            select(RuntimeNodeSpec).where(RuntimeNodeSpec.node_id == node_id).where(RuntimeNodeSpec.owner_subject == subject)
        ).first()
        if spec is None:
            spec = _ensure_node_spec(db, subject, node)
        spec.drain_state = drain_state
        spec.last_reconcile_at = now
        spec.updated_at = now
        db.add(spec)
        db.add(NodeEvent(node_id=node_id, event_type=f"node.{drain_state}", payload_json=_json_dump({"node_id": node_id})))
        db.commit()
        db.refresh(spec)
        return {"spec": _node_spec_payload(spec)}


def _node_payload(node: RuntimeNode) -> dict:
    return {
        "id": node.id,
        "owner_subject": node.owner_subject,
        "node_name": node.node_name,
        "hostname": node.hostname,
        "status": node.status,
        "trust_tier": node.trust_tier,
        "labels": json.loads(node.labels_json or "{}"),
        "capacity": json.loads(node.capacity_json or "{}"),
        "capabilities": json.loads(node.capabilities_json or "[]"),
        "runtime_version": node.runtime_version,
        "last_heartbeat_at": node.last_heartbeat_at,
        "registered_at": node.registered_at,
        "updated_at": node.updated_at,
    }


def _job_payload(job: RuntimeJob) -> dict:
    return {
        "id": job.id,
        "owner_subject": job.owner_subject,
        "mission_id": job.mission_id,
        "task_id": job.task_id,
        "runtime_session_id": job.runtime_session_id,
        "runtime_class": job.runtime_class,
        "image": job.image,
        "command": job.command,
        "args": json.loads(job.args_json or "[]"),
        "env": json.loads(job.env_json or "{}"),
        "cwd": job.cwd,
        "mounts": json.loads(job.mounts_json or "[]"),
        "artifact_rules": json.loads(job.artifact_rules_json or "{}"),
        "timeout_seconds": job.timeout_seconds,
        "restart_policy": job.restart_policy,
        "required_capabilities": json.loads(job.required_capabilities_json or "[]"),
        "preferred_labels": json.loads(job.preferred_labels_json or "{}"),
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _lease_payload(lease: JobLease) -> dict:
    return {
        "id": lease.id,
        "job_id": lease.job_id,
        "node_id": lease.node_id,
        "status": lease.status,
        "claimed_at": lease.claimed_at,
        "heartbeat_at": lease.heartbeat_at,
        "started_at": lease.started_at,
        "finished_at": lease.finished_at,
        "exit_code": lease.exit_code,
        "error_message": lease.error_message,
        "cleanup_status": lease.cleanup_status,
        "created_at": lease.created_at,
        "updated_at": lease.updated_at,
    }


def _session_from_token(token: str):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.utcnow()
    with get_session() as db:
        row = db.exec(
            select(UserSession)
            .where(UserSession.token_hash == token_hash)
            .where(UserSession.revoked == False)  # noqa: E712
            .where(UserSession.expires_at > now)
        ).first()
        if row is None:
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or expired session token")
        return {
            "subject": row.subject,
            "session_id": row.id,
            "expires_at": row.expires_at,
        }


def _execution_session_payload(session: ExecutionSession) -> dict:
    return {
        "id": session.id,
        "lease_id": session.lease_id,
        "runtime_class": session.runtime_class,
        "pty_requested": session.pty_requested,
        "attach_token_prefix": session.attach_token_prefix,
        "status": session.status,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


@router.post("/tokens", status_code=201)
@router.post("/join-tokens", status_code=201, include_in_schema=False)
def create_join_token(body: JoinTokenCreate, request: Request):
    subject = actor_subject_from_request(request)
    token_id = str(uuid.uuid4())
    now = datetime.utcnow()
    join_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(seconds=body.expires_in_seconds)
    token = RuntimeJoinToken(
        id=token_id,
        owner_subject=subject,
        token_hash=_hash_token(join_token),
        config_json=_json_dump(body.config),
        upgrade_channel=body.upgrade_channel.strip() or "stable",
        desired_version=body.desired_version.strip(),
        expires_at=expires_at,
        status="active",
        rotation_count=0,
        created_at=now,
        updated_at=now,
    )
    with get_session() as db:
        db.add(token)
        db.commit()
        db.refresh(token)
        payload = _join_token_payload(token)
        payload["join_token"] = join_token
        payload["expires_in_seconds"] = body.expires_in_seconds
        return payload


@router.post("/tokens/{token_id}/rotate")
@router.post("/join-tokens/{token_id}/rotate", include_in_schema=False)
def rotate_join_token(token_id: str, body: JoinTokenRotate, request: Request):
    subject = actor_subject_from_request(request)
    now = datetime.utcnow()
    with get_session() as db:
        token = _find_join_token(db, token_id, subject)
        join_token = secrets.token_urlsafe(32)
        token.token_hash = _hash_token(join_token)
        if body.expires_in_seconds:
            token.expires_at = now + timedelta(seconds=body.expires_in_seconds)
        token.rotation_count += 1
        token.status = "active"
        token.updated_at = now
        db.add(token)
        db.commit()
        db.refresh(token)
        payload = _join_token_payload(token)
        payload["join_token"] = join_token
        return payload


@router.get("/tokens/{token_id}")
@router.get("/join-tokens/{token_id}", include_in_schema=False)
def get_join_token(token_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        token = _find_join_token(db, token_id, subject)
        payload = _join_token_payload(token)
        return payload


@router.get("/nodes/{node_id}/config")
def get_node_config(node_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        node = db.get(RuntimeNode, node_id)
        if not node or node.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Node not found")
        spec = db.exec(
            select(RuntimeNodeSpec).where(RuntimeNodeSpec.node_id == node_id).where(RuntimeNodeSpec.owner_subject == subject)
        ).first()
        created = spec is None
        if spec is None:
            spec = _ensure_node_spec(db, subject, node)
        if created:
            db.commit()
        return _node_config_payload(node, spec)


@router.get("/nodes/{node_id}/install-bundle")
def get_node_install_bundle(node_id: str, request: Request):
    subject = actor_subject_from_request(request)
    base_url = str(request.base_url).rstrip("/")
    with get_session() as db:
        node = db.get(RuntimeNode, node_id)
        if not node or node.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Node not found")
        spec = db.exec(
            select(RuntimeNodeSpec).where(RuntimeNodeSpec.node_id == node_id).where(RuntimeNodeSpec.owner_subject == subject)
        ).first()
        if spec is None:
            spec = _ensure_node_spec(db, subject, node)
            db.commit()
        token = db.exec(
            select(RuntimeJoinToken)
            .where(RuntimeJoinToken.node_id == node_id)
            .where(RuntimeJoinToken.owner_subject == subject)
            .order_by(RuntimeJoinToken.updated_at.desc())
        ).first()
        return _node_install_bundle(node, spec, base_url, token)


@router.get("/nodes/{node_id}/install-script", response_class=PlainTextResponse)
def get_node_install_script(node_id: str, request: Request):
    subject = actor_subject_from_request(request)
    base_url = str(request.base_url).rstrip("/")
    with get_session() as db:
        node = db.get(RuntimeNode, node_id)
        if not node or node.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Node not found")
        return _node_install_script(base_url, node_id)


@router.get("/releases/latest.json")
def get_runtime_release_manifest():
    os_name = "linux"
    arch = "x86_64"
    file_name = _runtime_release_file(os_name, arch)
    return {
        "version": RUNTIME_RELEASE_VERSION,
        "files": [
            {
                "os": os_name,
                "arch": arch,
                "url": f"{RUNTIME_RELEASE_BASE_URL}/{file_name}" if file_name else "",
                "sha256": None,
            }
        ],
    }


@router.get("/releases/latest/download")
def download_runtime_release():
    file_name = _runtime_release_file("linux", "x86_64")
    if not file_name:
        raise HTTPException(status_code=404, detail="Release artifact not configured")
    return RedirectResponse(url=f"{RUNTIME_RELEASE_BASE_URL}/{file_name}", status_code=307)


@router.post("/nodes/{node_id}/reconcile")
def reconcile_node(node_id: str, body: NodeReconcile, request: Request):
    subject = actor_subject_from_request(request)
    now = datetime.utcnow()
    with get_session() as db:
        node = db.get(RuntimeNode, node_id)
        if not node or node.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Node not found")
        spec = db.exec(
            select(RuntimeNodeSpec).where(RuntimeNodeSpec.node_id == node_id).where(RuntimeNodeSpec.owner_subject == subject)
        ).first()
        if spec is None:
            spec = _ensure_node_spec(db, subject, node)
        if body.desired_version:
            spec.desired_version = body.desired_version.strip()
        if body.drain_state:
            spec.drain_state = body.drain_state
        if body.health_summary:
            spec.health_summary = body.health_summary
        spec.last_reconcile_at = now
        spec.updated_at = now
        db.add(spec)
        db.add(
            NodeEvent(
                node_id=node_id,
                event_type="node.spec.reconcile",
                payload_json=_json_dump(
                    {"node_id": node_id, "desired_version": spec.desired_version, "drain_state": spec.drain_state}
                ),
            )
        )
        db.commit()
        db.refresh(spec)
        return {"spec": _node_spec_payload(spec)}


@router.post("/nodes/{node_id}/cordon")
def cordon_node(node_id: str, request: Request):
    return _mutate_node_spec_state(node_id, request, "cordoned")


@router.post("/nodes/{node_id}/drain")
def drain_node(node_id: str, request: Request):
    return _mutate_node_spec_state(node_id, request, "draining")


@router.post("/nodes/{node_id}/upgrade")
def upgrade_node(node_id: str, request: Request):
    return _mutate_node_spec_state(node_id, request, "upgrading")


@router.get("/channels")
def list_channels():
    return {
        "channels": [
            {"name": "stable"},
            {"name": "latest"},
            {"name": "testing"},
        ]
    }


@router.post("/nodes/register", status_code=201)
def register_node(body: NodeRegister, request: Request):
    subject = actor_subject_from_request(request)
    node_id = str(uuid.uuid4())
    now = datetime.utcnow()
    with get_session() as db:
        existing = db.exec(select(RuntimeNode).where(RuntimeNode.node_name == body.node_name)).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Node '{body.node_name}' already exists")
        token = _validate_join_token(db, body.bootstrap_token, subject)
        node = RuntimeNode(
            id=node_id,
            owner_subject=subject,
            node_name=body.node_name.strip(),
            hostname=body.hostname.strip(),
            trust_tier=body.trust_tier.strip() or "untrusted",
            labels_json=_json_dump(body.labels),
            capacity_json=_json_dump(body.capacity),
            capabilities_json=_json_dump(body.capabilities),
            runtime_version=body.runtime_version.strip() or token.desired_version,
            bootstrap_token_prefix=body.bootstrap_token[:8],
            status="online",
            last_heartbeat_at=now,
            registered_at=now,
            updated_at=now,
        )
        db.add(node)
        _ensure_node_spec(
            db,
            subject,
            node,
            token,
            json.loads(token.config_json or "{}"),
        )
        token.node_id = node_id
        token.updated_at = now
        db.add(token)
        db.add(
            NodeEvent(
                node_id=node_id,
                event_type="node.registered",
                payload_json=_json_dump({"node_name": body.node_name, "runtime_version": body.runtime_version}),
            )
        )
        db.commit()
        db.refresh(node)
        return _node_payload(node)


@router.get("/nodes")
def list_nodes(request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        rows = db.exec(select(RuntimeNode).where(RuntimeNode.owner_subject == subject).order_by(RuntimeNode.updated_at.desc())).all()
        return {"nodes": [_node_payload(row) for row in rows]}


@router.post("/nodes/{node_id}/heartbeat")
def heartbeat_node(node_id: str, body: NodeHeartbeat, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        node = db.get(RuntimeNode, node_id)
        if not node or node.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Node not found")
        node.status = body.status
        node.last_heartbeat_at = datetime.utcnow()
        node.updated_at = datetime.utcnow()
        if body.labels is not None:
            node.labels_json = _json_dump(body.labels)
        if body.capacity is not None:
            node.capacity_json = _json_dump(body.capacity)
        if body.capabilities is not None:
            node.capabilities_json = _json_dump(body.capabilities)
        if body.runtime_version is not None:
            node.runtime_version = body.runtime_version
        _ensure_node_spec(db, subject, node)
        db.add(node)
        db.add(NodeEvent(node_id=node_id, event_type="node.heartbeat", payload_json=_json_dump(body.model_dump())))
        db.commit()
        db.refresh(node)
        return _node_payload(node)


@router.post("/jobs", status_code=201)
def create_job(body: JobCreate, request: Request):
    subject = actor_subject_from_request(request)
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    with get_session() as db:
        job = RuntimeJob(
            id=job_id,
            owner_subject=subject,
            mission_id=body.mission_id.strip(),
            task_id=body.task_id,
            runtime_session_id=body.runtime_session_id.strip(),
            runtime_class=body.runtime_class.strip() or "container",
            image=body.image.strip(),
            command=body.command,
            args_json=_json_dump(body.args),
            env_json=_json_dump(body.env),
            cwd=body.cwd.strip(),
            mounts_json=_json_dump(body.mounts),
            artifact_rules_json=_json_dump(body.artifact_rules),
            timeout_seconds=body.timeout_seconds,
            restart_policy=body.restart_policy.strip() or "never",
            required_capabilities_json=_json_dump(body.required_capabilities),
            preferred_labels_json=_json_dump(body.preferred_labels),
            status="queued",
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        db.add(NodeEvent(event_type="job.created", payload_json=_json_dump({"job_id": job_id, "runtime_class": job.runtime_class})))
        db.commit()
        db.refresh(job)
        return _job_payload(job)


@router.get("/jobs")
def list_jobs(request: Request, status: Optional[str] = None):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        stmt = select(RuntimeJob).where(RuntimeJob.owner_subject == subject)
        if status:
            stmt = stmt.where(RuntimeJob.status == status)
        rows = db.exec(stmt.order_by(RuntimeJob.created_at.desc())).all()
        return {"jobs": [_job_payload(row) for row in rows]}


@router.post("/jobs/{job_id}/leases", status_code=201)
def create_lease(job_id: str, body: LeaseCreate, request: Request):
    subject = actor_subject_from_request(request)
    now = datetime.utcnow()
    with get_session() as db:
        job = db.get(RuntimeJob, job_id)
        node = db.get(RuntimeNode, body.node_id)
        if not job or job.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Job not found")
        if not node or node.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Node not found")
        lease = JobLease(
            id=str(uuid.uuid4()),
            job_id=job_id,
            node_id=body.node_id,
            status="leased",
            claimed_at=now,
            heartbeat_at=now,
            created_at=now,
            updated_at=now,
        )
        job.status = "leased"
        db.add(lease)
        db.add(job)
        db.add(NodeEvent(node_id=node.id, lease_id=lease.id, event_type="lease.created", payload_json=_json_dump({"job_id": job_id})))
        db.commit()
        db.refresh(lease)
        return _lease_payload(lease)


@router.post("/nodes/{node_id}/leases/claim")
def claim_lease(node_id: str, request: Request):
    subject = actor_subject_from_request(request)
    now = datetime.utcnow()
    with get_session() as db:
        node = db.get(RuntimeNode, node_id)
        if not node or node.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Node not found")
        job = db.exec(
            select(RuntimeJob)
            .where(RuntimeJob.owner_subject == subject)
            .where(RuntimeJob.status == "queued")
            .order_by(RuntimeJob.created_at.asc())
        ).first()
        if job is None:
            return {"claimed": False, "lease": None, "job": None}
        lease = JobLease(
            id=str(uuid.uuid4()),
            job_id=job.id,
            node_id=node_id,
            status="leased",
            claimed_at=now,
            heartbeat_at=now,
            created_at=now,
            updated_at=now,
        )
        job.status = "leased"
        db.add(lease)
        db.add(job)
        db.add(NodeEvent(node_id=node_id, lease_id=lease.id, event_type="lease.claimed", payload_json=_json_dump({"job_id": job.id})))
        db.commit()
        db.refresh(lease)
        return {"claimed": True, "lease": _lease_payload(lease), "job": _job_payload(job)}


@router.post("/leases/{lease_id}/status")
def update_lease_status(lease_id: str, body: LeaseStatus, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        lease = db.get(JobLease, lease_id)
        if not lease:
            raise HTTPException(status_code=404, detail="Lease not found")
        job = db.get(RuntimeJob, lease.job_id)
        if not job or job.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Job not found")
        lease.status = body.status
        lease.heartbeat_at = body.heartbeat_at or datetime.utcnow()
        lease.updated_at = datetime.utcnow()
        if body.started_at is not None:
            lease.started_at = body.started_at
        db.add(lease)
        db.add(NodeEvent(node_id=lease.node_id, lease_id=lease.id, event_type="lease.status", payload_json=_json_dump(body.model_dump(mode="json"))))
        db.commit()
        db.refresh(lease)
        return _lease_payload(lease)


@router.post("/leases/{lease_id}/complete")
def complete_lease(lease_id: str, body: LeaseComplete, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        lease = db.get(JobLease, lease_id)
        if not lease:
            raise HTTPException(status_code=404, detail="Lease not found")
        job = db.get(RuntimeJob, lease.job_id)
        if not job or job.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Job not found")
        lease.status = "completed" if body.exit_code == 0 else "failed"
        lease.exit_code = body.exit_code
        lease.error_message = body.error_message
        lease.finished_at = datetime.utcnow()
        lease.updated_at = datetime.utcnow()
        job.status = lease.status
        job.updated_at = datetime.utcnow()
        db.add(lease)
        db.add(job)
        db.add(NodeEvent(node_id=lease.node_id, lease_id=lease.id, event_type="lease.complete", payload_json=_json_dump(body.model_dump())))
        db.commit()
        db.refresh(lease)
        return _lease_payload(lease)


@router.post("/leases/{lease_id}/logs")
def append_lease_logs(lease_id: str, body: LeaseLogChunk, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        lease = db.get(JobLease, lease_id)
        if not lease:
            raise HTTPException(status_code=404, detail="Lease not found")
        job = db.get(RuntimeJob, lease.job_id)
        if not job or job.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Job not found")
        db.add(
            NodeEvent(
                node_id=lease.node_id,
                lease_id=lease.id,
                event_type=f"lease.log.{body.stream}",
                payload_json=_json_dump(body.model_dump()),
            )
        )
        db.commit()
        return {"ok": True}


@router.post("/execution-sessions", status_code=201)
def create_execution_session(body: ExecutionSessionCreate, request: Request):
    subject = actor_subject_from_request(request)
    now = datetime.utcnow()
    with get_session() as db:
        lease = db.get(JobLease, body.lease_id)
        if not lease:
            raise HTTPException(status_code=404, detail="Lease not found")
        job = db.get(RuntimeJob, lease.job_id)
        if not job or job.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Job not found")
        session = ExecutionSession(
            id=str(uuid.uuid4()),
            lease_id=body.lease_id,
            runtime_class=body.runtime_class,
            pty_requested=body.pty_requested,
            attach_token_prefix=secrets.token_hex(4),
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return _execution_session_payload(session)


@router.get("/execution-sessions/{session_id}/attach-token")
def get_execution_session_attach_token(session_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        session = db.get(ExecutionSession, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Execution session not found")
        lease = db.get(JobLease, session.lease_id)
        if not lease:
            raise HTTPException(status_code=404, detail="Lease not found")
        job = db.get(RuntimeJob, lease.job_id)
        if not job or job.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "id": session.id,
            "lease_id": session.lease_id,
            "attach_token_prefix": session.attach_token_prefix,
            "status": session.status,
        }


@router.post("/execution-sessions/{session_id}/attach")
def attach_execution_session(session_id: str, body: ExecutionAttachCreate, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        session = db.get(ExecutionSession, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Execution session not found")
        lease = db.get(JobLease, session.lease_id)
        if not lease:
            raise HTTPException(status_code=404, detail="Lease not found")
        job = db.get(RuntimeJob, lease.job_id)
        if not job or job.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Job not found")
        db.add(
            NodeEvent(
                node_id=lease.node_id,
                lease_id=lease.id,
                event_type=f"execution.attach.{body.stream}",
                payload_json=_json_dump(body.model_dump()),
            )
        )
        db.commit()
        return {"ok": True}


@router.websocket("/execution-sessions/{session_id}/pty")
async def execution_session_pty(websocket: WebSocket, session_id: str):
    token = websocket.query_params.get("token") or websocket.headers.get("authorization", "")
    if token.startswith("Bearer "):
        token = token.split(" ", 1)[1].strip()
    if not token:
        await websocket.close(code=1008)
        return
    try:
        principal = _session_from_token(token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    with get_session() as db:
        session = db.get(ExecutionSession, session_id)
        if not session:
            await websocket.close(code=1008)
            return
        lease = db.get(JobLease, session.lease_id)
        if not lease:
            await websocket.close(code=1008)
            return
        job = db.get(RuntimeJob, lease.job_id)
        if not job or job.owner_subject != principal["subject"]:
            await websocket.close(code=1008)
            return

    await websocket.accept()
    clients = _execution_ws_clients.setdefault(session_id, set())
    clients.add(websocket)
    try:
        while True:
            payload = await websocket.receive_json()
            for client in list(_execution_ws_clients.get(session_id, set())):
                if client is websocket:
                    continue
                try:
                    await client.send_json(payload)
                except Exception:
                    continue
    except WebSocketDisconnect:
        pass
    finally:
        clients = _execution_ws_clients.get(session_id)
        if clients is not None:
            clients.discard(websocket)
            if not clients:
                _execution_ws_clients.pop(session_id, None)


async def broadcast_execution_session(session_id: str, payload: dict) -> None:
    clients = list(_execution_ws_clients.get(session_id, set()))
    for client in clients:
        try:
            await client.send_json(payload)
        except Exception:
            continue


@router.get("/leases/{lease_id}")
def get_lease(lease_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        lease = db.get(JobLease, lease_id)
        if not lease:
            raise HTTPException(status_code=404, detail="Lease not found")
        job = db.get(RuntimeJob, lease.job_id)
        if not job or job.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Job not found")
        return _lease_payload(lease)
