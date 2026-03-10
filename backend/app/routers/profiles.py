import base64
import hashlib
import json
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from app.db import get_session
from app.models import UserProfile
from app.schemas import (
    UserProfileCreate,
    UserProfileDownloadRead,
    UserProfileRead,
    UserProfileUpdate,
)
from app.services.authz import actor_subject_from_request
from app.services.pagination import bounded_limit, limit_query

router = APIRouter(tags=["profiles"])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def _validate_name(name: str) -> None:
    if not _SLUG_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="Profile name must be lowercase alphanumeric with hyphens/underscores (1-63 chars, start with alphanumeric)",
        )


def _profile_read(p: UserProfile) -> UserProfileRead:
    return UserProfileRead(
        id=p.id,
        name=p.name,
        owner_subject=p.owner_subject,
        description=p.description,
        is_default=p.is_default,
        manifest=json.loads(p.manifest_json or "[]"),
        sha256=p.sha256,
        size_bytes=p.size_bytes,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _get_owned_profile(session, owner_subject: str, name: str) -> UserProfile:
    profile = session.exec(
        select(UserProfile)
        .where(UserProfile.owner_subject == owner_subject)
        .where(UserProfile.name == name)
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def _clear_default(session, owner_subject: str, exclude_id: int | None = None) -> None:
    stmt = select(UserProfile).where(UserProfile.owner_subject == owner_subject).where(UserProfile.is_default == True)  # noqa: E712
    if exclude_id is not None:
        stmt = stmt.where(UserProfile.id != exclude_id)
    others = session.exec(stmt).all()
    for other in others:
        other.is_default = False
        other.updated_at = datetime.utcnow()
        session.add(other)


def _compute_tarball_fields(tarball_b64: str) -> tuple[str, int]:
    raw = base64.b64decode(tarball_b64)
    return hashlib.sha256(raw).hexdigest(), len(raw)


@router.get("/me/profiles", response_model=list[UserProfileRead])
def list_profiles(request: Request, limit: int = limit_query()):
    owner_subject = actor_subject_from_request(request)
    with get_session() as session:
        profiles = session.exec(
            select(UserProfile).where(UserProfile.owner_subject == owner_subject)
            .order_by(UserProfile.updated_at.desc())
            .limit(bounded_limit(limit))
        ).all()
        return [_profile_read(p) for p in profiles]


@router.post("/me/profiles", response_model=UserProfileRead, status_code=201)
def create_profile(payload: UserProfileCreate, request: Request):
    owner_subject = actor_subject_from_request(request)
    _validate_name(payload.name)
    with get_session() as session:
        existing = session.exec(
            select(UserProfile)
            .where(UserProfile.owner_subject == owner_subject)
            .where(UserProfile.name == payload.name)
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="Profile with this name already exists")

        sha256, size_bytes = _compute_tarball_fields(payload.tarball_b64)

        if payload.is_default:
            _clear_default(session, owner_subject)

        profile = UserProfile(
            name=payload.name,
            owner_subject=owner_subject,
            description=payload.description,
            is_default=payload.is_default,
            manifest_json=json.dumps(payload.manifest),
            tarball_b64=payload.tarball_b64,
            sha256=sha256,
            size_bytes=size_bytes,
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return _profile_read(profile)


@router.get("/me/profiles/{name}", response_model=UserProfileRead)
def get_profile(name: str, request: Request):
    owner_subject = actor_subject_from_request(request)
    with get_session() as session:
        profile = _get_owned_profile(session, owner_subject, name)
        return _profile_read(profile)


@router.put("/me/profiles/{name}", response_model=UserProfileRead)
def replace_profile(name: str, payload: UserProfileCreate, request: Request):
    owner_subject = actor_subject_from_request(request)
    with get_session() as session:
        profile = _get_owned_profile(session, owner_subject, name)
        sha256, size_bytes = _compute_tarball_fields(payload.tarball_b64)

        if payload.is_default:
            _clear_default(session, owner_subject, exclude_id=profile.id)

        profile.description = payload.description
        profile.is_default = payload.is_default
        profile.manifest_json = json.dumps(payload.manifest)
        profile.tarball_b64 = payload.tarball_b64
        profile.sha256 = sha256
        profile.size_bytes = size_bytes
        profile.updated_at = datetime.utcnow()
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return _profile_read(profile)


@router.patch("/me/profiles/{name}", response_model=UserProfileRead)
def patch_profile(name: str, payload: UserProfileUpdate, request: Request):
    owner_subject = actor_subject_from_request(request)
    with get_session() as session:
        profile = _get_owned_profile(session, owner_subject, name)

        if payload.description is not None:
            profile.description = payload.description
        if payload.is_default is not None:
            if payload.is_default:
                _clear_default(session, owner_subject, exclude_id=profile.id)
            profile.is_default = payload.is_default
        if payload.manifest is not None:
            profile.manifest_json = json.dumps(payload.manifest)
        if payload.tarball_b64 is not None:
            profile.sha256, profile.size_bytes = _compute_tarball_fields(payload.tarball_b64)
            profile.tarball_b64 = payload.tarball_b64

        profile.updated_at = datetime.utcnow()
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return _profile_read(profile)


@router.delete("/me/profiles/{name}", status_code=204)
def delete_profile(name: str, request: Request):
    owner_subject = actor_subject_from_request(request)
    with get_session() as session:
        profile = _get_owned_profile(session, owner_subject, name)
        session.delete(profile)
        session.commit()


@router.get("/me/profiles/{name}/download", response_model=UserProfileDownloadRead)
def download_profile(name: str, request: Request):
    owner_subject = actor_subject_from_request(request)
    with get_session() as session:
        profile = _get_owned_profile(session, owner_subject, name)
        if not profile.tarball_b64:
            raise HTTPException(status_code=404, detail="Profile has no bundle")
        return UserProfileDownloadRead(
            id=profile.id,
            name=profile.name,
            owner_subject=profile.owner_subject,
            description=profile.description,
            is_default=profile.is_default,
            manifest=json.loads(profile.manifest_json or "[]"),
            sha256=profile.sha256,
            size_bytes=profile.size_bytes,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            tarball_b64=profile.tarball_b64,
        )


@router.post("/me/profiles/{name}/activate", response_model=UserProfileRead)
def activate_profile(name: str, request: Request):
    owner_subject = actor_subject_from_request(request)
    with get_session() as session:
        profile = _get_owned_profile(session, owner_subject, name)
        _clear_default(session, owner_subject, exclude_id=profile.id)
        profile.is_default = True
        profile.updated_at = datetime.utcnow()
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return _profile_read(profile)
