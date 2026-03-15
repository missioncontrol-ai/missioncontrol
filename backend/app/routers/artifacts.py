from datetime import datetime, timezone
import hashlib
import json
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
        source_uri = artifact_data.get("uri", "")
        if object_storage_enabled() and source_uri and not source_uri.startswith("s3://"):
            key = build_scoped_key(
                mission_id=kluster.mission_id or "unassigned",
                kluster_id=artifact.kluster_id,
                entity="artifacts",
                filename=f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_slugify(artifact.name)}.json",
            )
            blob = json.dumps(
                {
                    "name": artifact.name,
                    "artifact_type": artifact.artifact_type,
                    "source_uri": source_uri,
                    "status": artifact.status,
                    "provenance": artifact.provenance,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            persisted_uri, size_bytes = put_bytes(key=key, body=blob, content_type="application/json")
            artifact.uri = persisted_uri
            artifact.storage_backend = "s3"
            artifact.size_bytes = size_bytes
            artifact.mime_type = "application/json"
            artifact.content_sha256 = hashlib.sha256(blob).hexdigest()
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
        if artifact.storage_backend != "s3" or not artifact.uri.startswith("s3://"):
            raise HTTPException(status_code=409, detail="Artifact does not have retrievable S3-backed content")
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
        for key in ("storage_backend", "content_sha256", "size_bytes", "mime_type"):
            if key in incoming_updates:
                updates[key] = incoming_updates.get(key)
        for k, v in updates.items():
            setattr(artifact, k, v)
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
