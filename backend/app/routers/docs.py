from datetime import datetime, timezone
import hashlib
import logging
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select
from app.db import get_session
from app.models import Doc, Kluster
from app.schemas import DocCreate, DocRead, DocUpdate
from app.services.authz import (
    assert_mission_reader_or_admin,
    assert_mission_writer_or_admin,
    is_platform_admin,
    readable_mission_ids_for_request,
)
from app.services.vectorstore import index_doc
from app.services.git_ledger import enqueue_ledger_event, actor_subject_from_request, request_source
from app.services.schema_pack import enforce_schema_pack
from app.services.governance import extract_approval_context, require_policy_action
from app.services.object_storage import build_scoped_key, object_storage_enabled, put_bytes

router = APIRouter(prefix="/docs", tags=["docs"])
logger = logging.getLogger(__name__)


@router.post("", response_model=DocRead)
def create_doc(payload: DocCreate, request: Request):
    doc_data = enforce_schema_pack(
        request=request,
        entity_type="doc",
        payload=payload.model_dump(),
        operation="create",
    )
    doc = Doc(**doc_data)
    with get_session() as session:
        require_policy_action(
            session=session,
            action="doc.create",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        kluster = session.get(Kluster, payload.kluster_id)
        if not kluster:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if kluster.mission_id:
            assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        if object_storage_enabled():
            key = build_scoped_key(
                mission_id=kluster.mission_id or "unassigned",
                kluster_id=doc.kluster_id,
                entity="docs",
                filename=f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{doc.title.strip().lower().replace(' ', '-')[:48] or 'doc'}.md",
            )
            body_bytes = doc.body.encode("utf-8")
            s3_uri, size_bytes = put_bytes(key=key, body=body_bytes, content_type="text/markdown; charset=utf-8")
            meta = {
                "storage_backend": "s3",
                "s3_uri": s3_uri,
                "size_bytes": size_bytes,
                "sha256": hashlib.sha256(body_bytes).hexdigest(),
            }
            existing = {}
            if doc.provenance:
                try:
                    existing = json.loads(doc.provenance)
                except Exception:
                    existing = {"note": doc.provenance}
            existing["object_storage"] = meta
            doc.provenance = json.dumps(existing, separators=(",", ":"))
        session.add(doc)
        session.commit()
        session.refresh(doc)

        try:
            index_doc(
                doc.id,
                f"{doc.title}\n{doc.body}",
                {"kluster_id": doc.kluster_id, "type": doc.doc_type},
            )
        except Exception as exc:
            # Indexing is secondary; persist docs even if vector store is degraded.
            logger.exception("doc index failed on create doc_id=%s: %s", doc.id, exc)
        try:
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=doc.kluster_id,
                entity_type="doc",
                entity_id=doc.id,
                action="create",
                before=None,
                after=doc.model_dump(),
                actor_subject=actor_subject_from_request(request),
                source=request_source(request),
            )
        except Exception as exc:
            logger.exception("doc ledger enqueue failed on create doc_id=%s: %s", doc.id, exc)
        session.refresh(doc)
        return doc


@router.get("", response_model=list[DocRead])
def list_docs(kluster_id: Optional[str] = None, request: Request = None):
    with get_session() as session:
        stmt = select(Doc)
        if kluster_id is not None:
            stmt = stmt.where(Doc.kluster_id == kluster_id)
        docs = session.exec(stmt.order_by(Doc.updated_at.desc())).all()
        if is_platform_admin(request):
            return docs
        readable_ids = readable_mission_ids_for_request(session=session, request=request)
        if not readable_ids:
            return []
        kluster_ids = {doc.kluster_id for doc in docs if doc.kluster_id}
        klusters = session.exec(select(Kluster).where(Kluster.id.in_(kluster_ids))).all() if kluster_ids else []
        mission_by_kluster = {k.id: k.mission_id for k in klusters}
        docs = [doc for doc in docs if mission_by_kluster.get(doc.kluster_id) in readable_ids]
        return docs


@router.get("/{doc_id}", response_model=DocRead)
def get_doc(doc_id: int, request: Request):
    with get_session() as session:
        doc = session.get(Doc, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Doc not found")
        kluster = session.get(Kluster, doc.kluster_id)
        if not kluster:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if kluster.mission_id:
            assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        elif not is_platform_admin(request):
            raise HTTPException(status_code=403, detail="Forbidden: mission viewer, contributor, or owner required")
        return doc


@router.patch("/{doc_id}", response_model=DocRead)
def update_doc(doc_id: int, payload: DocUpdate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="doc.update",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        doc = session.get(Doc, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Doc not found")
        before = doc.model_dump()
        kluster = session.get(Kluster, doc.kluster_id)
        if kluster and kluster.mission_id:
            assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        updates = enforce_schema_pack(
            request=request,
            entity_type="doc",
            payload=payload.model_dump(exclude_unset=True),
            operation="update",
        )
        for k, v in updates.items():
            setattr(doc, k, v)
        doc.updated_at = datetime.now(timezone.utc)
        doc.version += 1
        if object_storage_enabled():
            key = build_scoped_key(
                mission_id=kluster.mission_id or "unassigned",
                kluster_id=doc.kluster_id,
                entity="docs",
                filename=f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{doc.title.strip().lower().replace(' ', '-')[:48] or 'doc'}.md",
            )
            body_bytes = doc.body.encode("utf-8")
            s3_uri, size_bytes = put_bytes(key=key, body=body_bytes, content_type="text/markdown; charset=utf-8")
            meta = {
                "storage_backend": "s3",
                "s3_uri": s3_uri,
                "size_bytes": size_bytes,
                "sha256": hashlib.sha256(body_bytes).hexdigest(),
            }
            existing = {}
            if doc.provenance:
                try:
                    existing = json.loads(doc.provenance)
                except Exception:
                    existing = {"note": doc.provenance}
            existing["object_storage"] = meta
            doc.provenance = json.dumps(existing, separators=(",", ":"))
        session.add(doc)
        session.commit()
        session.refresh(doc)

        try:
            index_doc(
                doc.id,
                f"{doc.title}\n{doc.body}",
                {"kluster_id": doc.kluster_id, "type": doc.doc_type},
            )
        except Exception as exc:
            logger.exception("doc index failed on update doc_id=%s: %s", doc.id, exc)
        try:
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id if kluster else None,
                kluster_id=doc.kluster_id,
                entity_type="doc",
                entity_id=doc.id,
                action="update",
                before=before,
                after=doc.model_dump(),
                actor_subject=actor_subject_from_request(request),
                source=request_source(request),
            )
        except Exception as exc:
            logger.exception("doc ledger enqueue failed on update doc_id=%s: %s", doc.id, exc)
        session.refresh(doc)
        return doc


@router.post("/{doc_id}/publish", response_model=DocRead)
def publish_doc(doc_id: int, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="doc.publish",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        doc = session.get(Doc, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Doc not found")
        before = doc.model_dump()
        kluster = session.get(Kluster, doc.kluster_id)
        if kluster and kluster.mission_id:
            assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        doc.status = "published"
        doc.updated_at = datetime.now(timezone.utc)
        doc.version += 1
        session.add(doc)
        session.commit()
        session.refresh(doc)
        try:
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id if kluster else None,
                kluster_id=doc.kluster_id,
                entity_type="doc",
                entity_id=doc.id,
                action="publish",
                before=before,
                after=doc.model_dump(),
                actor_subject=actor_subject_from_request(request),
                source=request_source(request),
            )
        except Exception as exc:
            logger.exception("doc ledger enqueue failed on publish doc_id=%s: %s", doc.id, exc)
        session.refresh(doc)
        return doc


@router.delete("/{doc_id}")
def delete_doc(doc_id: int, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="doc.delete",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        doc = session.get(Doc, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Doc not found")
        before = doc.model_dump()
        kluster = session.get(Kluster, doc.kluster_id)
        if not kluster:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if kluster.mission_id:
            assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
        elif not is_platform_admin(request):
            raise HTTPException(status_code=403, detail="Forbidden: mission contributor, owner, or admin required")

        session.delete(doc)
        session.commit()
        try:
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=doc.kluster_id,
                entity_type="doc",
                entity_id=doc_id,
                action="delete",
                before=before,
                after=None,
                actor_subject=actor_subject_from_request(request),
                source=request_source(request),
            )
        except Exception as exc:
            logger.exception("doc ledger enqueue failed on delete doc_id=%s: %s", doc_id, exc)
        return {"ok": True, "deleted_id": doc_id}
