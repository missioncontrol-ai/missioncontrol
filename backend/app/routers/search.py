from fastapi import APIRouter, Request
from sqlmodel import select
from app.db import get_session
from app.models import Doc, Kluster, Task
from app.services.authz import is_platform_admin, readable_mission_ids_for_request
from app.services.vectorstore import query_tasks, query_docs

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/tasks")
def search_tasks(q: str, limit: int = 5, request: Request = None):
    with get_session() as session:
        raw_results = query_tasks(q, limit=limit)
        if is_platform_admin(request):
            return {"results": raw_results}
        readable_ids = readable_mission_ids_for_request(session=session, request=request)
        if not readable_ids:
            return {"results": []}
        task_ids = [int(item["id"]) for item in raw_results if item.get("id") is not None]
        if not task_ids:
            return {"results": []}
        task_rows = session.exec(select(Task).where(Task.id.in_(task_ids))).all()
        kluster_ids = {row.kluster_id for row in task_rows if row.kluster_id}
        kluster_rows = session.exec(select(Kluster).where(Kluster.id.in_(kluster_ids))).all() if kluster_ids else []
        mission_by_kluster = {k.id: k.mission_id for k in kluster_rows}
        allowed_task_ids = {
            row.id
            for row in task_rows
            if row.id is not None and mission_by_kluster.get(row.kluster_id) in readable_ids
        }
        return {"results": [item for item in raw_results if int(item["id"]) in allowed_task_ids]}


@router.get("/docs")
def search_docs(q: str, limit: int = 5, request: Request = None):
    with get_session() as session:
        raw_results = query_docs(q, limit=limit)
        if is_platform_admin(request):
            return {"results": raw_results}
        readable_ids = readable_mission_ids_for_request(session=session, request=request)
        if not readable_ids:
            return {"results": []}
        doc_ids = [int(item["id"]) for item in raw_results if item.get("id") is not None]
        if not doc_ids:
            return {"results": []}
        doc_rows = session.exec(select(Doc).where(Doc.id.in_(doc_ids))).all()
        kluster_ids = {row.kluster_id for row in doc_rows if row.kluster_id}
        kluster_rows = session.exec(select(Kluster).where(Kluster.id.in_(kluster_ids))).all() if kluster_ids else []
        mission_by_kluster = {k.id: k.mission_id for k in kluster_rows}
        allowed_doc_ids = {
            row.id
            for row in doc_rows
            if row.id is not None and mission_by_kluster.get(row.kluster_id) in readable_ids
        }
        return {"results": [item for item in raw_results if int(item["id"]) in allowed_doc_ids]}


@router.get("/klusters")
def search_klusters(q: str, limit: int = 5, request: Request = None):
    query = (q or "").lower()
    with get_session() as session:
        readable_ids = readable_mission_ids_for_request(session=session, request=request)
        admin = is_platform_admin(request)
        klusters = session.exec(select(Kluster)).all()
        matches = [
            c
            for c in klusters
            if query in c.name.lower() or query in (c.tags or "").lower()
        ]
        if not admin:
            matches = [c for c in matches if c.mission_id in readable_ids]
        matches = matches[:limit]
        return {"results": [c.dict() for c in matches]}
