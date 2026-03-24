import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import select
from app.db import get_session
from app.models import Artifact, Kluster
from app.schemas import ArtifactCreate, ArtifactRead, ArtifactUpdate
from app.services.authz import (
    assert_mission_reader_or_admin,
    assert_mission_writer_or_admin,
    is_platform_admin,
    readable_mission_ids_for_request,
)
from app.services.git_ledger import (
    enqueue_ledger_event,
    actor_subject_from_request,
    publish_pending_ledger_events,
    request_source,
)
from app.services.persistence_publish import PublishRoutingError
from app.services.schema_pack import enforce_schema_pack
from app.services.governance import extract_approval_context, require_policy_action
from app.services.object_storage import (
    build_scoped_key,
    get_bytes_from_uri,
    object_storage_enabled,
    presign_get_uri,
    put_bytes,
    scoped_prefix,
)
from app.services.pagination import bounded_limit, limit_query

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "artifact"


def _inline_threshold_bytes() -> int:
    raw = (os.getenv("MC_ARTIFACT_INLINE_THRESHOLD_BYTES") or "").strip()
    if not raw:
        return 512 * 1024
    try:
        return max(1024, int(raw))
    except Exception:
        return 512 * 1024


@router.post("", response_model=ArtifactRead)
def create_artifact(payload: ArtifactCreate, request: Request):
    raw_payload = payload.model_dump()
    schema_payload = {
        "kluster_id": raw_payload.get("kluster_id"),
        "name": raw_payload.get("name"),
        "artifact_type": raw_payload.get("artifact_type"),
        "uri": raw_payload.get("uri"),
        "status": raw_payload.get("status"),
        "provenance": raw_payload.get("provenance"),
    }
    artifact_data = enforce_schema_pack(
        request=request,
        entity_type="artifact",
        payload=schema_payload,
        operation="create",
    )
    for key in ("storage_backend", "content_sha256", "size_bytes", "mime_type"):
        if key in raw_payload:
            artifact_data[key] = raw_payload.get(key)
    artifact_data["storage_class"] = str(raw_payload.get("storage_class") or "")
    artifact_data["external_pointer"] = bool(raw_payload.get("external_pointer") or False)
    artifact_data["external_uri"] = str(raw_payload.get("external_uri") or "")
    content_b64 = str(raw_payload.get("content_b64") or "").strip()
    if content_b64:
        artifact_data["content_b64"] = content_b64
    if artifact_data.get("uri", "").startswith("s3://") and not artifact_data.get("storage_backend"):
        artifact_data["storage_backend"] = "s3"
    artifact = Artifact(**artifact_data)
    with get_session() as session:
        require_policy_action(
            session=session,
            action="artifact.create",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        kluster = session.get(Kluster, payload.kluster_id)
        if not kluster:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if kluster.mission_id:
            assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        source_uri = str(artifact_data.get("uri", "") or "").strip()
        content_b64 = str(artifact_data.get("content_b64") or "").strip()
        external_pointer = bool(artifact_data.get("external_pointer") or False)
        external_uri = str(artifact_data.get("external_uri") or "").strip()

        if content_b64 and external_pointer:
            raise HTTPException(status_code=422, detail="content_b64 and external_pointer cannot both be set")

        if content_b64:
            try:
                body = base64.b64decode(content_b64, validate=True)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Invalid content_b64: {exc}") from exc
            mime_type = str(artifact.mime_type or "application/octet-stream")
            artifact.size_bytes = len(body)
            artifact.content_sha256 = hashlib.sha256(body).hexdigest()
            threshold = _inline_threshold_bytes()
            use_s3 = object_storage_enabled() and (len(body) > threshold or mime_type.startswith("image/"))
            if use_s3:
                key = build_scoped_key(
                    mission_id=kluster.mission_id or "unassigned",
                    kluster_id=artifact.kluster_id,
                    entity="artifacts",
                    filename=f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_slugify(artifact.name)}",
                )
                persisted_uri, size_bytes = put_bytes(key=key, body=body, content_type=mime_type)
                artifact.uri = persisted_uri
                artifact.storage_backend = "s3"
                artifact.storage_class = "s3_primary"
                artifact.size_bytes = size_bytes
                artifact.content_b64 = None
                artifact.external_pointer = False
                artifact.external_uri = ""
            else:
                artifact.uri = source_uri or artifact.uri or f"db-inline://artifacts/{artifact.content_sha256}"
                artifact.storage_backend = "inline"
                artifact.storage_class = "db_inline"
                artifact.content_b64 = content_b64
                artifact.external_pointer = False
                artifact.external_uri = ""
        elif external_pointer or external_uri:
            resolved_uri = external_uri or source_uri
            if not resolved_uri:
                raise HTTPException(status_code=422, detail="external pointer mode requires external_uri or uri")
            artifact.uri = resolved_uri
            artifact.external_pointer = True
            artifact.external_uri = resolved_uri
            artifact.storage_class = "external_pointer"
            artifact.storage_backend = artifact.storage_backend or "external"
            artifact.content_b64 = None
        elif object_storage_enabled() and source_uri and source_uri.startswith("s3://"):
            artifact.storage_backend = "s3"
            artifact.storage_class = artifact.storage_class or "s3_primary"
            artifact.external_pointer = False
        session.add(artifact)
        session.commit()
        session.refresh(artifact)
        enqueue_ledger_event(
            session=session,
            mission_id=kluster.mission_id,
            kluster_id=artifact.kluster_id,
            entity_type="artifact",
            entity_id=artifact.id,
            action="create",
            before=None,
            after=artifact.model_dump(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        session.refresh(artifact)
        return artifact


@router.get("", response_model=list[ArtifactRead])
def list_artifacts(kluster_id: Optional[str] = None, request: Request = None, limit: int = limit_query()):
    with get_session() as session:
        stmt = select(Artifact)
        if kluster_id is not None:
            stmt = stmt.where(Artifact.kluster_id == kluster_id)
        artifacts = session.exec(stmt.order_by(Artifact.updated_at.desc()).limit(bounded_limit(limit))).all()
        if is_platform_admin(request):
            return artifacts
        readable_ids = readable_mission_ids_for_request(session=session, request=request)
        if not readable_ids:
            return []
        kluster_ids = {artifact.kluster_id for artifact in artifacts if artifact.kluster_id}
        klusters = session.exec(select(Kluster).where(Kluster.id.in_(kluster_ids))).all() if kluster_ids else []
        mission_by_kluster = {k.id: k.mission_id for k in klusters}
        artifacts = [artifact for artifact in artifacts if mission_by_kluster.get(artifact.kluster_id) in readable_ids]
        return artifacts


@router.get("/{artifact_id}", response_model=ArtifactRead)
def get_artifact(artifact_id: int, request: Request):
    with get_session() as session:
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        kluster = session.get(Kluster, artifact.kluster_id)
        if not kluster:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if kluster.mission_id:
            assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        elif not is_platform_admin(request):
            raise HTTPException(status_code=403, detail="Forbidden: mission viewer, contributor, or owner required")
        return artifact


@router.get("/{artifact_id}/content")
def get_artifact_content(artifact_id: int, request: Request):
    with get_session() as session:
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        kluster = session.get(Kluster, artifact.kluster_id)
        if not kluster:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if kluster.mission_id:
            assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        elif not is_platform_admin(request):
            raise HTTPException(status_code=403, detail="Forbidden: mission viewer, contributor, or owner required")
        if artifact.storage_backend == "s3" and artifact.uri.startswith("s3://"):
            try:
                expected = scoped_prefix(mission_id=kluster.mission_id or "unassigned", kluster_id=artifact.kluster_id)
                body, content_type = get_bytes_from_uri(artifact.uri, expected_prefix=expected)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid S3 URI: {exc}") from exc
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"S3 retrieval failed: {exc}") from exc
            return Response(content=body, media_type=content_type)
        if artifact.content_b64:
            try:
                body = base64.b64decode(artifact.content_b64, validate=True)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Invalid inline artifact content: {exc}") from exc
            return Response(content=body, media_type=(artifact.mime_type or "application/octet-stream"))
        raise HTTPException(status_code=409, detail="Artifact does not have retrievable managed content")


@router.get("/{artifact_id}/download-url")
def get_artifact_download_url(
    artifact_id: int,
    request: Request,
    expires_seconds: int = 60,
):
    with get_session() as session:
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        kluster = session.get(Kluster, artifact.kluster_id)
        if not kluster:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if kluster.mission_id:
            assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        elif not is_platform_admin(request):
            raise HTTPException(status_code=403, detail="Forbidden: mission viewer, contributor, or owner required")
        if artifact.storage_backend != "s3" or not artifact.uri.startswith("s3://"):
            raise HTTPException(status_code=409, detail="Artifact does not have retrievable S3-backed content")
        try:
            expected = scoped_prefix(mission_id=kluster.mission_id or "unassigned", kluster_id=artifact.kluster_id)
            url = presign_get_uri(artifact.uri, expires_seconds=expires_seconds, expected_prefix=expected)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid S3 URI: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"S3 presign failed: {exc}") from exc
        ttl = max(1, min(int(expires_seconds), 3600))
        return {"artifact_id": artifact.id, "uri": artifact.uri, "expires_seconds": ttl, "download_url": url}


@router.patch("/{artifact_id}", response_model=ArtifactRead)
def update_artifact(artifact_id: int, payload: ArtifactUpdate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="artifact.update",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        before = artifact.model_dump()
        kluster = session.get(Kluster, artifact.kluster_id)
        if kluster and kluster.mission_id:
            assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        incoming_updates = payload.model_dump(exclude_unset=True)
        schema_updates = {
            key: value
            for key, value in incoming_updates.items()
            if key in {"name", "artifact_type", "uri", "status", "provenance"}
        }
        updates = enforce_schema_pack(
            request=request,
            entity_type="artifact",
            payload=schema_updates,
            operation="update",
        )
        for key in (
            "storage_backend",
            "content_sha256",
            "size_bytes",
            "mime_type",
            "storage_class",
            "external_pointer",
            "external_uri",
        ):
            if key in incoming_updates:
                updates[key] = incoming_updates.get(key)
        for k, v in updates.items():
            setattr(artifact, k, v)
        content_b64 = str(incoming_updates.get("content_b64") or "").strip()
        if content_b64:
            try:
                body = base64.b64decode(content_b64, validate=True)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Invalid content_b64: {exc}") from exc
            mime_type = str(incoming_updates.get("mime_type") or artifact.mime_type or "application/octet-stream")
            artifact.size_bytes = len(body)
            artifact.content_sha256 = hashlib.sha256(body).hexdigest()
            threshold = _inline_threshold_bytes()
            if object_storage_enabled() and (len(body) > threshold or mime_type.startswith("image/")):
                key = build_scoped_key(
                    mission_id=kluster.mission_id if kluster and kluster.mission_id else "unassigned",
                    kluster_id=artifact.kluster_id,
                    entity="artifacts",
                    filename=f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_slugify(artifact.name)}",
                )
                persisted_uri, size_bytes = put_bytes(key=key, body=body, content_type=mime_type)
                artifact.uri = persisted_uri
                artifact.storage_backend = "s3"
                artifact.storage_class = "s3_primary"
                artifact.size_bytes = size_bytes
                artifact.content_b64 = None
            else:
                artifact.storage_backend = "inline"
                artifact.storage_class = "db_inline"
                artifact.content_b64 = content_b64
                if not artifact.uri:
                    artifact.uri = f"db-inline://artifacts/{artifact.content_sha256}"
        artifact.updated_at = datetime.now(timezone.utc)
        artifact.version += 1
        session.add(artifact)
        session.commit()
        session.refresh(artifact)
        enqueue_ledger_event(
            session=session,
            mission_id=kluster.mission_id if kluster else None,
            kluster_id=artifact.kluster_id,
            entity_type="artifact",
            entity_id=artifact.id,
            action="update",
            before=before,
            after=artifact.model_dump(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        session.refresh(artifact)
        return artifact


@router.post("/{artifact_id}/publish", response_model=ArtifactRead)
def publish_artifact(artifact_id: int, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="artifact.publish",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        before = artifact.model_dump()

        kluster = session.get(Kluster, artifact.kluster_id)
        if not kluster:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if kluster.mission_id:
            assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        artifact.status = "published"
        artifact.updated_at = datetime.now(timezone.utc)
        artifact.version += 1

        session.add(artifact)
        session.commit()
        session.refresh(artifact)
        event = enqueue_ledger_event(
            session=session,
            mission_id=kluster.mission_id,
            kluster_id=artifact.kluster_id,
            entity_type="artifact",
            entity_id=artifact.id,
            action="publish",
            before=before,
            after=artifact.model_dump(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        if kluster.mission_id:
            try:
                publish_result = publish_pending_ledger_events(
                    session=session,
                    mission_id=kluster.mission_id,
                    actor_subject=actor_subject_from_request(request),
                )
                artifact.provenance = json.dumps(
                    {
                        "publisher": "git",
                        "repo": publish_result.get("repo_url", ""),
                        "branch": publish_result.get("branch", ""),
                        "path": event.git_path,
                        "commit": event.git_commit,
                    },
                    separators=(",", ":"),
                )
                session.add(artifact)
                session.commit()
            except PublishRoutingError as exc:
                raise HTTPException(status_code=502, detail=f"Publish route failed: {exc}") from exc
        session.refresh(artifact)
        return artifact


@router.delete("/{artifact_id}")
def delete_artifact(artifact_id: int, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="artifact.delete",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        before = artifact.model_dump()
        kluster = session.get(Kluster, artifact.kluster_id)
        if not kluster:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if kluster.mission_id:
            assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        elif not is_platform_admin(request):
            raise HTTPException(status_code=403, detail="Forbidden: mission contributor, owner, or admin required")

        session.delete(artifact)
        session.commit()
        enqueue_ledger_event(
            session=session,
            mission_id=kluster.mission_id,
            kluster_id=artifact.kluster_id,
            entity_type="artifact",
            entity_id=artifact_id,
            action="delete",
            before=before,
            after=None,
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        return {"ok": True, "deleted_id": artifact_id}
