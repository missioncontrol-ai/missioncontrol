import hashlib
from typing import Optional

from sqlmodel import Session, select

from app.models import Task
from app.services.ids import is_hash_id


def task_public_id_for(task: Task) -> str:
    if is_hash_id(task.public_id):
        return task.public_id
    if task.id is None:
        return ""
    seed = f"{task.id}:{task.kluster_id}:{task.created_at.isoformat() if task.created_at else ''}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def ensure_task_public_id(session: Session, task: Task) -> str:
    public_id = task_public_id_for(task)
    if public_id and task.public_id != public_id:
        task.public_id = public_id
        session.add(task)
        session.commit()
        session.refresh(task)
    return public_id


def resolve_task_by_ref(
    *,
    session: Session,
    task_ref: str | int,
    kluster_id: Optional[str] = None,
) -> Task | None:
    ref_str = str(task_ref).strip()
    task: Task | None = None
    if is_hash_id(ref_str):
        stmt = select(Task).where(Task.public_id == ref_str)
        if kluster_id:
            stmt = stmt.where(Task.kluster_id == kluster_id)
        task = session.exec(stmt).first()
    if task is None:
        try:
            internal_id = int(ref_str)
        except ValueError:
            return None
        task = session.get(Task, internal_id)
        if task is not None and kluster_id and task.kluster_id != kluster_id:
            return None
    return task
