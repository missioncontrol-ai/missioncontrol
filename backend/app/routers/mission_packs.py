"""Mission pack router."""
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import Session, select

from app.db import engine
from app.models import MissionPack
from app.services.authz import actor_subject_from_request
from app.services.mission_pack import install_mission_pack, pack_mission

router = APIRouter()


@router.get("")
def list_packs(request: Request):
    subject = actor_subject_from_request(request)
    with Session(engine) as session:
        packs = list(session.exec(
            select(MissionPack).where(MissionPack.owner_subject == subject)
        ).all())
    return [
        {"id": p.id, "name": p.name, "version": p.version, "sha256": p.sha256[:8], "created_at": p.created_at}
        for p in packs
    ]


@router.get("/{pack_id}")
def get_pack(pack_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with Session(engine) as session:
        pack = session.exec(
            select(MissionPack).where(MissionPack.id == pack_id, MissionPack.owner_subject == subject)
        ).first()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")
    return {"id": pack.id, "name": pack.name, "version": pack.version,
            "manifest": json.loads(pack.manifest_json), "sha256": pack.sha256}


@router.delete("/{pack_id}")
def delete_pack(pack_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with Session(engine) as session:
        pack = session.exec(
            select(MissionPack).where(MissionPack.id == pack_id, MissionPack.owner_subject == subject)
        ).first()
        if not pack:
            raise HTTPException(status_code=404, detail="Pack not found")
        session.delete(pack)
        session.commit()
    return {"deleted": pack_id}


@router.post("/missions/{mission_id}/export")
def export_mission(mission_id: str, request: Request):
    subject = actor_subject_from_request(request)
    try:
        pack = pack_mission(mission_id, subject)
        return {"pack_id": pack.id, "name": pack.name, "sha256": pack.sha256}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{pack_id}/install")
def install_pack(pack_id: str, request: Request, target_mission_id: Optional[str] = None):
    subject = actor_subject_from_request(request)
    try:
        result = install_mission_pack(pack_id, subject, target_mission_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
