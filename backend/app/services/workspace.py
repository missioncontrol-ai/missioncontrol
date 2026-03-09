import base64
import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import Artifact, Doc, Kluster, Mission, SlackChannelBinding, WorkspaceLease
from app.services.git_ledger import enqueue_ledger_event
from app.services.governance import get_active_policy
from app.services.ids import new_hash_id
from app.services.keystone import (
    KLUSTER_KEYSTONE_FILENAME,
    MISSION_KEYSTONE_FILENAME,
    ensure_kluster_workstream,
    ensure_mission_northstar,
)
from app.services.object_storage import build_scoped_key, object_storage_enabled, put_bytes

LEASE_SECONDS_DEFAULT = 900


def _now() -> datetime:
    return datetime.utcnow()


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or fallback


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)


def _json_loads(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _doc_path(doc: Doc) -> str:
    return f"docs/{doc.id}-{_slug(doc.title, 'doc')}.md"


def _artifact_path(artifact: Artifact) -> str:
    return f"artifacts/{artifact.id}-{_slug(artifact.name, 'artifact')}.json"


def _doc_hash(doc: Doc) -> str:
    payload = {
        "title": doc.title,
        "body": doc.body,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "provenance": doc.provenance,
        "version": doc.version,
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _artifact_hash(artifact: Artifact) -> str:
    payload = {
        "name": artifact.name,
        "artifact_type": artifact.artifact_type,
        "uri": artifact.uri,
        "storage_backend": artifact.storage_backend,
        "content_sha256": artifact.content_sha256,
        "size_bytes": artifact.size_bytes,
        "mime_type": artifact.mime_type,
        "status": artifact.status,
        "provenance": artifact.provenance,
        "version": artifact.version,
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _mission_keystone_hash(mission: Mission) -> str:
    payload = {
        "filename": MISSION_KEYSTONE_FILENAME,
        "body": mission.northstar_md or "",
        "version": mission.northstar_version or 0,
        "modified_by": mission.northstar_modified_by or "",
        "modified_at": str(mission.northstar_modified_at or ""),
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _kluster_keystone_hash(kluster: Kluster) -> str:
    payload = {
        "filename": KLUSTER_KEYSTONE_FILENAME,
        "body": kluster.workstream_md or "",
        "version": kluster.workstream_version or 0,
        "modified_by": kluster.workstream_modified_by or "",
        "modified_at": str(kluster.workstream_modified_at or ""),
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def lease_is_expired(lease: WorkspaceLease) -> bool:
    return lease.expires_at <= _now()


def mark_expired_if_needed(session: Session, lease: WorkspaceLease) -> WorkspaceLease:
    if lease.status == "active" and lease_is_expired(lease):
        lease.status = "expired"
        lease.updated_at = _now()
        session.add(lease)
        session.commit()
        session.refresh(lease)
    return lease


def get_lease(session: Session, lease_id: str) -> WorkspaceLease | None:
    lease = session.get(WorkspaceLease, lease_id)
    if not lease:
        return None
    return mark_expired_if_needed(session, lease)


def assert_lease_owner_or_admin(lease: WorkspaceLease, actor_subject: str, *, is_admin: bool) -> None:
    if is_admin:
        return
    if lease.actor_subject != actor_subject:
        raise HTTPException(status_code=403, detail="Forbidden: lease owner or platform admin required")


def _workspace_state(index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": "mc-workspace/v1",
        "created_at": _now().isoformat(),
        "index": index,
    }


def build_workspace_snapshot(*, session: Session, mission_id: str, kluster_id: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    mission = session.get(Mission, mission_id)
    kluster_row = session.get(Kluster, kluster_id)
    if not mission or not kluster_row:
        raise HTTPException(status_code=404, detail="Mission or kluster not found")
    if mission.id != kluster_row.mission_id:
        raise HTTPException(status_code=409, detail="Kluster is not linked to mission")
    changed = False
    changed = ensure_mission_northstar(mission, actor="system") or changed
    changed = ensure_kluster_workstream(kluster_row, actor="system") or changed
    if changed:
        session.add(mission)
        session.add(kluster_row)
        session.commit()
        session.refresh(mission)
        session.refresh(kluster_row)

    docs = session.exec(select(Doc).where(Doc.kluster_id == kluster_id).order_by(Doc.updated_at.desc())).all()
    artifacts = session.exec(
        select(Artifact).where(Artifact.kluster_id == kluster_id).order_by(Artifact.updated_at.desc())
    ).all()
    bindings = session.exec(
        select(SlackChannelBinding).where(SlackChannelBinding.mission_id == mission_id).order_by(SlackChannelBinding.id.desc())
    ).all()
    policy = get_active_policy(session)

    index: dict[str, dict[str, Any]] = {}
    docs_out: list[dict[str, Any]] = []
    for doc in docs:
        path = _doc_path(doc)
        base_hash = _doc_hash(doc)
        index[path] = {
            "kind": "doc",
            "entity_id": doc.id,
            "version": doc.version,
            "base_hash": base_hash,
            "readonly": False,
        }
        docs_out.append(
            {
                "id": doc.id,
                "path": path,
                "title": doc.title,
                "doc_type": doc.doc_type,
                "status": doc.status,
                "version": doc.version,
                "base_hash": base_hash,
            }
        )

    artifacts_out: list[dict[str, Any]] = []
    for artifact in artifacts:
        path = _artifact_path(artifact)
        base_hash = _artifact_hash(artifact)
        index[path] = {
            "kind": "artifact",
            "entity_id": artifact.id,
            "version": artifact.version,
            "base_hash": base_hash,
            "readonly": False,
        }
        artifacts_out.append(
            {
                "id": artifact.id,
                "path": path,
                "name": artifact.name,
                "artifact_type": artifact.artifact_type,
                "uri": artifact.uri,
                "storage_backend": artifact.storage_backend,
                "content_sha256": artifact.content_sha256,
                "size_bytes": artifact.size_bytes,
                "mime_type": artifact.mime_type,
                "status": artifact.status,
                "version": artifact.version,
                "base_hash": base_hash,
                "fetch_mode": "s3" if artifact.uri.startswith("s3://") else "uri",
            }
        )

    connections_path = "connections.json"
    policies_path = "policies.json"
    connections_payload = [
        {
            "provider": row.provider,
            "mission_id": row.mission_id,
            "workspace_external_id": row.workspace_external_id,
            "channel_id": row.channel_id,
            "channel_name": row.channel_name,
            "metadata": _json_loads(row.channel_metadata_json),
        }
        for row in bindings
    ]
    connections_hash = hashlib.sha256(_json_dumps({"connections": connections_payload}).encode("utf-8")).hexdigest()
    policies_hash = hashlib.sha256(_json_dumps(policy).encode("utf-8")).hexdigest()
    index[connections_path] = {
        "kind": "connections",
        "entity_id": mission_id,
        "version": 1,
        "base_hash": connections_hash,
        "readonly": True,
    }
    index[policies_path] = {
        "kind": "policy",
        "entity_id": mission_id,
        "version": 1,
        "base_hash": policies_hash,
        "readonly": True,
    }

    mission_keystone_path = MISSION_KEYSTONE_FILENAME
    mission_keystone_hash = _mission_keystone_hash(mission)
    index[mission_keystone_path] = {
        "kind": "mission_keystone",
        "entity_id": mission.id,
        "version": mission.northstar_version,
        "base_hash": mission_keystone_hash,
        "readonly": False,
    }
    kluster_keystone_path = KLUSTER_KEYSTONE_FILENAME
    kluster_keystone_hash = _kluster_keystone_hash(kluster_row)
    index[kluster_keystone_path] = {
        "kind": "kluster_keystone",
        "entity_id": kluster_row.id,
        "version": kluster_row.workstream_version,
        "base_hash": kluster_keystone_hash,
        "readonly": False,
    }

    snapshot = {
        "mission_id": mission_id,
        "kluster_id": kluster_id,
        "docs": docs_out,
        "artifacts": artifacts_out,
        "connections": connections_payload,
        "policies": policy,
        "mission_keystone": {
            "filename": mission_keystone_path,
            "content": mission.northstar_md,
            "version": mission.northstar_version,
            "modified_by": mission.northstar_modified_by,
            "modified_at": mission.northstar_modified_at,
            "base_hash": mission_keystone_hash,
        },
        "kluster_keystone": {
            "filename": kluster_keystone_path,
            "content": kluster_row.workstream_md,
            "version": kluster_row.workstream_version,
            "modified_by": kluster_row.workstream_modified_by,
            "modified_at": kluster_row.workstream_modified_at,
            "base_hash": kluster_keystone_hash,
        },
        "workspace_files": sorted(index.keys()),
        "workspace_state": _workspace_state(index),
    }
    return snapshot, index


def create_workspace_lease(
    *,
    session: Session,
    mission_id: str,
    kluster_id: str,
    actor_subject: str,
    agent_id: str = "",
    workspace_label: str = "",
    lease_seconds: int = LEASE_SECONDS_DEFAULT,
) -> tuple[WorkspaceLease, dict[str, Any]]:
    snapshot, index = build_workspace_snapshot(session=session, mission_id=mission_id, kluster_id=kluster_id)
    now = _now()
    ttl = max(60, min(int(lease_seconds), 3600))
    lease = WorkspaceLease(
        id=new_hash_id(),
        mission_id=mission_id,
        kluster_id=kluster_id,
        actor_subject=actor_subject,
        agent_id=agent_id,
        workspace_label=workspace_label,
        status="active",
        base_snapshot_json=_json_dumps({"index": index}),
        lease_seconds=ttl,
        last_heartbeat_at=now,
        expires_at=now + timedelta(seconds=ttl),
        created_at=now,
        updated_at=now,
    )
    while session.get(WorkspaceLease, lease.id):
        lease.id = new_hash_id()
    session.add(lease)
    session.commit()
    session.refresh(lease)
    return lease, snapshot


def heartbeat_workspace_lease(*, session: Session, lease: WorkspaceLease) -> WorkspaceLease:
    lease = mark_expired_if_needed(session, lease)
    if lease.status != "active":
        raise HTTPException(status_code=409, detail="Workspace lease is not active")
    now = _now()
    lease.last_heartbeat_at = now
    lease.expires_at = now + timedelta(seconds=max(60, min(int(lease.lease_seconds), 3600)))
    lease.updated_at = now
    session.add(lease)
    session.commit()
    session.refresh(lease)
    return lease


def release_workspace_lease(*, session: Session, lease: WorkspaceLease, reason: str = "") -> WorkspaceLease:
    lease = mark_expired_if_needed(session, lease)
    if lease.status in {"released", "expired"}:
        return lease
    now = _now()
    lease.status = "released"
    lease.release_reason = (reason or "").strip()
    lease.released_at = now
    lease.updated_at = now
    session.add(lease)
    session.commit()
    session.refresh(lease)
    return lease


def commit_workspace_changes(
    *,
    session: Session,
    lease: WorkspaceLease,
    changes: list[dict[str, Any]],
    request,
    actor_subject: str,
    source: str,
) -> dict[str, Any]:
    lease = mark_expired_if_needed(session, lease)
    if lease.status != "active":
        raise HTTPException(status_code=409, detail="Workspace lease is not active")
    if not isinstance(changes, list) or not changes:
        raise HTTPException(status_code=422, detail="change_set must be a non-empty list")

    base_snapshot = _json_loads(lease.base_snapshot_json)
    index = base_snapshot.get("index") if isinstance(base_snapshot.get("index"), dict) else {}
    conflicts: list[dict[str, Any]] = []
    ops: list[dict[str, Any]] = []

    for item in changes:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="change_set entries must be objects")
        path = str(item.get("path") or "").strip()
        if not path:
            raise HTTPException(status_code=422, detail="change_set entry missing path")
        entry = index.get(path)
        if not isinstance(entry, dict):
            raise HTTPException(status_code=422, detail=f"path not found in workspace snapshot: {path}")
        if bool(entry.get("readonly")):
            conflicts.append(
                {
                    "path": path,
                    "reason": "readonly_snapshot",
                    "message": "connections/policies snapshots are read-only in workspace commit",
                }
            )
            continue

        kind = str(entry.get("kind") or "")
        entity_id_raw = entry.get("entity_id")
        base_hash = str(entry.get("base_hash") or "")
        base_version = int(entry.get("version") or 0)

        if kind == "doc":
            entity_id = int(entity_id_raw or 0)
            row = session.get(Doc, entity_id)
            if not row:
                conflicts.append({"path": path, "reason": "missing_entity", "message": "Doc not found"})
                continue
            current_hash = _doc_hash(row)
            if row.version != base_version or current_hash != base_hash:
                conflicts.append(
                    {
                        "path": path,
                        "reason": "base_mismatch",
                        "expected_version": base_version,
                        "current_version": row.version,
                        "expected_hash": base_hash,
                        "current_hash": current_hash,
                    }
                )
                continue
            ops.append({"kind": "doc", "path": path, "row": row, "change": item})
            continue

        if kind == "artifact":
            entity_id = int(entity_id_raw or 0)
            row = session.get(Artifact, entity_id)
            if not row:
                conflicts.append({"path": path, "reason": "missing_entity", "message": "Artifact not found"})
                continue
            current_hash = _artifact_hash(row)
            if row.version != base_version or current_hash != base_hash:
                conflicts.append(
                    {
                        "path": path,
                        "reason": "base_mismatch",
                        "expected_version": base_version,
                        "current_version": row.version,
                        "expected_hash": base_hash,
                        "current_hash": current_hash,
                    }
                )
                continue
            ops.append({"kind": "artifact", "path": path, "row": row, "change": item})
            continue

        if kind == "mission_keystone":
            row = session.get(Mission, str(entry.get("entity_id") or lease.mission_id))
            if not row:
                conflicts.append({"path": path, "reason": "missing_entity", "message": "Mission not found"})
                continue
            current_hash = _mission_keystone_hash(row)
            if int(row.northstar_version or 0) != base_version or current_hash != base_hash:
                conflicts.append(
                    {
                        "path": path,
                        "reason": "base_mismatch",
                        "expected_version": base_version,
                        "current_version": int(row.northstar_version or 0),
                        "expected_hash": base_hash,
                        "current_hash": current_hash,
                    }
                )
                continue
            ops.append({"kind": "mission_keystone", "path": path, "row": row, "change": item})
            continue

        if kind == "kluster_keystone":
            row = session.get(Kluster, str(entry.get("entity_id") or lease.kluster_id))
            if not row:
                conflicts.append({"path": path, "reason": "missing_entity", "message": "Kluster not found"})
                continue
            current_hash = _kluster_keystone_hash(row)
            if int(row.workstream_version or 0) != base_version or current_hash != base_hash:
                conflicts.append(
                    {
                        "path": path,
                        "reason": "base_mismatch",
                        "expected_version": base_version,
                        "current_version": int(row.workstream_version or 0),
                        "expected_hash": base_hash,
                        "current_hash": current_hash,
                    }
                )
                continue
            ops.append({"kind": "kluster_keystone", "path": path, "row": row, "change": item})
            continue

        conflicts.append({"path": path, "reason": "unsupported_kind", "kind": kind})

    if conflicts:
        return {"ok": False, "applied_count": 0, "conflicts": conflicts}

    applied: list[dict[str, Any]] = []
    kluster = session.get(Kluster, lease.kluster_id)
    for op in ops:
        if op["kind"] == "mission_keystone":
            mission_row: Mission = op["row"]
            change = op["change"]
            new_content = str(change.get("content") or "")
            if not new_content.strip():
                raise HTTPException(status_code=422, detail=f"{MISSION_KEYSTONE_FILENAME} content cannot be empty")
            before = mission_row.model_dump()
            mission_row.northstar_md = new_content
            mission_row.northstar_version = int(mission_row.northstar_version or 0) + 1
            mission_row.northstar_modified_by = actor_subject
            mission_row.northstar_modified_at = _now()
            mission_row.updated_at = _now()
            session.add(mission_row)
            session.commit()
            session.refresh(mission_row)
            enqueue_ledger_event(
                session=session,
                mission_id=lease.mission_id,
                kluster_id=lease.kluster_id,
                entity_type="mission",
                entity_id=mission_row.id,
                action="workspace_commit",
                before=before,
                after=mission_row.model_dump(),
                actor_subject=actor_subject,
                source=source,
            )
            applied.append(
                {
                    "path": op["path"],
                    "kind": "mission_keystone",
                    "id": mission_row.id,
                    "version": mission_row.northstar_version,
                    "base_hash": _mission_keystone_hash(mission_row),
                }
            )
            continue

        if op["kind"] == "kluster_keystone":
            kluster_row: Kluster = op["row"]
            change = op["change"]
            new_content = str(change.get("content") or "")
            if not new_content.strip():
                raise HTTPException(status_code=422, detail=f"{KLUSTER_KEYSTONE_FILENAME} content cannot be empty")
            before = kluster_row.model_dump()
            kluster_row.workstream_md = new_content
            kluster_row.workstream_version = int(kluster_row.workstream_version or 0) + 1
            kluster_row.workstream_modified_by = actor_subject
            kluster_row.workstream_modified_at = _now()
            kluster_row.updated_at = _now()
            session.add(kluster_row)
            session.commit()
            session.refresh(kluster_row)
            enqueue_ledger_event(
                session=session,
                mission_id=lease.mission_id,
                kluster_id=lease.kluster_id,
                entity_type="kluster",
                entity_id=kluster_row.id,
                action="workspace_commit",
                before=before,
                after=kluster_row.model_dump(),
                actor_subject=actor_subject,
                source=source,
            )
            applied.append(
                {
                    "path": op["path"],
                    "kind": "kluster_keystone",
                    "id": kluster_row.id,
                    "version": kluster_row.workstream_version,
                    "base_hash": _kluster_keystone_hash(kluster_row),
                }
            )
            continue

        if op["kind"] == "doc":
            doc: Doc = op["row"]
            change = op["change"]
            before = doc.model_dump()
            if "content" in change:
                doc.body = str(change.get("content") or "")
            if "title" in change:
                doc.title = str(change.get("title") or "")
            if "doc_type" in change:
                doc.doc_type = str(change.get("doc_type") or "")
            if "status" in change:
                doc.status = str(change.get("status") or "")
            if "provenance" in change:
                doc.provenance = str(change.get("provenance") or "")
            doc.version += 1
            doc.updated_at = _now()
            session.add(doc)
            session.commit()
            session.refresh(doc)
            enqueue_ledger_event(
                session=session,
                mission_id=lease.mission_id,
                kluster_id=lease.kluster_id,
                entity_type="doc",
                entity_id=doc.id,
                action="workspace_commit",
                before=before,
                after=doc.model_dump(),
                actor_subject=actor_subject,
                source=source,
            )
            applied.append(
                {
                    "path": op["path"],
                    "kind": "doc",
                    "id": doc.id,
                    "version": doc.version,
                    "base_hash": _doc_hash(doc),
                }
            )
            continue

        artifact: Artifact = op["row"]
        change = op["change"]
        before = artifact.model_dump()
        fields = change.get("fields")
        if isinstance(fields, dict):
            for key in (
                "name",
                "artifact_type",
                "uri",
                "storage_backend",
                "content_sha256",
                "size_bytes",
                "mime_type",
                "status",
                "provenance",
            ):
                if key in fields:
                    setattr(artifact, key, fields[key])

        content_b64 = str(change.get("content_b64") or "").strip()
        if content_b64:
            if not object_storage_enabled():
                raise HTTPException(status_code=409, detail="object storage is not configured for artifact content uploads")
            if not kluster or not kluster.mission_id:
                raise HTTPException(status_code=409, detail="artifact kluster is not linked to a mission")
            try:
                content_bytes = base64.b64decode(content_b64, validate=True)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Invalid content_b64: {exc}") from exc
            key = build_scoped_key(
                mission_id=kluster.mission_id,
                kluster_id=artifact.kluster_id,
                entity="artifacts",
                filename=f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{_slug(artifact.name, 'artifact')}",
            )
            mime_type = str(change.get("mime_type") or artifact.mime_type or "application/octet-stream")
            s3_uri, size_bytes = put_bytes(key=key, body=content_bytes, content_type=mime_type)
            artifact.uri = s3_uri
            artifact.storage_backend = "s3"
            artifact.size_bytes = size_bytes
            artifact.mime_type = mime_type
            artifact.content_sha256 = hashlib.sha256(content_bytes).hexdigest()

        artifact.version += 1
        artifact.updated_at = _now()
        session.add(artifact)
        session.commit()
        session.refresh(artifact)
        enqueue_ledger_event(
            session=session,
            mission_id=lease.mission_id,
            kluster_id=lease.kluster_id,
            entity_type="artifact",
            entity_id=artifact.id,
            action="workspace_commit",
            before=before,
            after=artifact.model_dump(),
            actor_subject=actor_subject,
            source=source,
        )
        applied.append(
            {
                "path": op["path"],
                "kind": "artifact",
                "id": artifact.id,
                "version": artifact.version,
                "base_hash": _artifact_hash(artifact),
            }
        )

    refreshed_snapshot, refreshed_index = build_workspace_snapshot(
        session=session,
        mission_id=lease.mission_id,
        kluster_id=lease.kluster_id,
    )
    lease.base_snapshot_json = _json_dumps({"index": refreshed_index})
    lease.updated_at = _now()
    session.add(lease)
    session.commit()
    session.refresh(lease)
    return {
        "ok": True,
        "applied_count": len(applied),
        "applied": applied,
        "workspace_snapshot": refreshed_snapshot,
    }
