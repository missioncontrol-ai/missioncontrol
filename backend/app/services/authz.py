import os
from datetime import datetime

from fastapi import HTTPException, Request
from sqlmodel import Session, select

from app.models import Mission, MissionRoleMembership

ROLE_OWNER = "mission_owner"
ROLE_CONTRIBUTOR = "mission_contributor"
ROLE_VIEWER = "mission_viewer"

_VALID_ROLES = {ROLE_OWNER, ROLE_CONTRIBUTOR, ROLE_VIEWER}


def actor_subject_from_request(request: Request | None) -> str:
    if request is None:
        return "unknown"
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, dict):
        return str(principal.get("email") or principal.get("subject") or "unknown")
    return "unknown"


def normalized_principal_identities(request: Request | None) -> set[str]:
    identities: set[str] = set()
    if request is None:
        return identities
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, dict):
        return identities
    for key in ("email", "subject"):
        value = principal.get(key)
        if value:
            identities.add(str(value).strip().lower())
    return identities


def is_platform_admin(request: Request | None) -> bool:
    if request is None:
        return False
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, dict):
        return False
    subject = str(principal.get("subject") or "").strip().lower()
    email = str(principal.get("email") or "").strip().lower()
    admin_subjects = {x.lower() for x in _split_csv(os.getenv("MC_ADMIN_SUBJECTS", ""))}
    admin_emails = {x.lower() for x in _split_csv(os.getenv("MC_ADMIN_EMAILS", ""))}
    return (subject and subject in admin_subjects) or (email and email in admin_emails)


def assert_platform_admin(request: Request | None) -> None:
    if not is_platform_admin(request):
        raise HTTPException(status_code=403, detail="Forbidden: platform admin required")


def assert_mission_owner_or_admin(*, session: Session, request: Request | None, mission_id: str) -> Mission:
    mission = session.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if is_platform_admin(request):
        return mission
    identities = normalized_principal_identities(request)
    if not identities:
        raise HTTPException(status_code=403, detail="Forbidden: mission owner required")

    legacy_owners = {x.lower() for x in _split_csv(mission.owners)}
    if legacy_owners & identities:
        return mission

    owner_rows = session.exec(
        select(MissionRoleMembership)
        .where(MissionRoleMembership.mission_id == mission_id)
        .where(MissionRoleMembership.role == ROLE_OWNER)
    ).all()
    role_owners = {r.subject.strip().lower() for r in owner_rows if r.subject}
    if role_owners & identities:
        return mission
    raise HTTPException(status_code=403, detail="Forbidden: mission owner required")


def assert_mission_writer_or_admin(*, session: Session, request: Request | None, mission_id: str) -> Mission:
    mission = session.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if is_platform_admin(request):
        return mission
    identities = normalized_principal_identities(request)
    if not identities:
        raise HTTPException(status_code=403, detail="Forbidden: mission contributor or owner required")

    allowed = {x.lower() for x in _split_csv(mission.owners)} | {x.lower() for x in _split_csv(mission.contributors)}
    rows = session.exec(
        select(MissionRoleMembership)
        .where(MissionRoleMembership.mission_id == mission_id)
        .where(MissionRoleMembership.role.in_([ROLE_OWNER, ROLE_CONTRIBUTOR]))
    ).all()
    allowed |= {r.subject.strip().lower() for r in rows if r.subject}
    if allowed & identities:
        return mission
    raise HTTPException(status_code=403, detail="Forbidden: mission contributor or owner required")


def assert_mission_reader_or_admin(*, session: Session, request: Request | None, mission_id: str) -> Mission:
    mission = session.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if is_platform_admin(request):
        return mission
    identities = normalized_principal_identities(request)
    if not identities:
        raise HTTPException(status_code=403, detail="Forbidden: mission viewer, contributor, or owner required")

    allowed = {x.lower() for x in _split_csv(mission.owners)} | {x.lower() for x in _split_csv(mission.contributors)}
    rows = session.exec(
        select(MissionRoleMembership)
        .where(MissionRoleMembership.mission_id == mission_id)
        .where(MissionRoleMembership.role.in_([ROLE_OWNER, ROLE_CONTRIBUTOR, ROLE_VIEWER]))
    ).all()
    allowed |= {r.subject.strip().lower() for r in rows if r.subject}
    if allowed & identities:
        return mission
    raise HTTPException(status_code=403, detail="Forbidden: mission viewer, contributor, or owner required")


def readable_mission_ids_for_request(*, session: Session, request: Request | None) -> set[str]:
    if is_platform_admin(request):
        return {mission_id for mission_id in session.exec(select(Mission.id)).all() if mission_id}
    identities = normalized_principal_identities(request)
    if not identities:
        return set()

    readable_ids: set[str] = set()
    role_rows = session.exec(
        select(MissionRoleMembership)
        .where(MissionRoleMembership.subject.in_(list(identities)))
        .where(MissionRoleMembership.role.in_([ROLE_OWNER, ROLE_CONTRIBUTOR, ROLE_VIEWER]))
    ).all()
    readable_ids |= {row.mission_id for row in role_rows if row.mission_id}

    for identity in identities:
        identity_like = f"%{identity}%"
        missions = session.exec(
            select(Mission.id).where(
                (Mission.owners.ilike(identity_like)) | (Mission.contributors.ilike(identity_like))
            )
        ).all()
        readable_ids |= {mission_id for mission_id in missions if mission_id}
    return readable_ids


def set_mission_owner(*, session: Session, mission: Mission, new_owner: str) -> Mission:
    owner = new_owner.strip()
    if not owner:
        raise HTTPException(status_code=422, detail="new_owner cannot be empty")
    now = datetime.utcnow()
    mission.owners = owner
    mission.updated_at = now
    session.add(mission)

    owner_rows = session.exec(
        select(MissionRoleMembership)
        .where(MissionRoleMembership.mission_id == mission.id)
        .where(MissionRoleMembership.role == ROLE_OWNER)
    ).all()
    for row in owner_rows:
        session.delete(row)
    session.add(
        MissionRoleMembership(
            mission_id=mission.id,
            subject=owner,
            role=ROLE_OWNER,
            created_at=now,
            updated_at=now,
        )
    )
    return mission


def upsert_mission_role(*, session: Session, mission_id: str, subject: str, role: str) -> MissionRoleMembership:
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of: {', '.join(sorted(_VALID_ROLES))}")
    subject_clean = subject.strip()
    if not subject_clean:
        raise HTTPException(status_code=422, detail="subject cannot be empty")
    existing = session.exec(
        select(MissionRoleMembership)
        .where(MissionRoleMembership.mission_id == mission_id)
        .where(MissionRoleMembership.subject == subject_clean)
    ).first()
    now = datetime.utcnow()
    if existing:
        existing.role = role
        existing.updated_at = now
        session.add(existing)
        return existing
    created = MissionRoleMembership(
        mission_id=mission_id,
        subject=subject_clean,
        role=role,
        created_at=now,
        updated_at=now,
    )
    session.add(created)
    return created


def remove_mission_role(*, session: Session, mission_id: str, subject: str) -> bool:
    subject_clean = subject.strip()
    if not subject_clean:
        return False
    row = session.exec(
        select(MissionRoleMembership)
        .where(MissionRoleMembership.mission_id == mission_id)
        .where(MissionRoleMembership.subject == subject_clean)
    ).first()
    if not row:
        return False
    session.delete(row)
    return True


def _split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]
