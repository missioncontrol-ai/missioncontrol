import json
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request

from app.db import get_session
from app.models import SkillBundle, SkillSnapshot
from app.schemas import (
    SkillBundleCreate,
    SkillBundleRead,
    SkillSnapshotDownloadRead,
    SkillSnapshotResolveRead,
    SkillSyncAck,
    SkillSyncStatusRead,
)
from app.services.authz import (
    actor_subject_from_request,
    assert_mission_owner_or_admin,
    assert_mission_reader_or_admin,
    assert_mission_writer_or_admin,
)
from app.services.git_ledger import enqueue_ledger_event, request_source
from app.services.governance import extract_approval_context, require_policy_action
from app.services.skills import (
    create_skill_bundle,
    get_sync_state,
    resolve_effective_snapshot,
    upsert_sync_state,
    validate_kluster_scope,
)

router = APIRouter(tags=["skills"])


def _bundle_read_payload(bundle) -> SkillBundleRead:
    return SkillBundleRead(
        id=bundle.id,
        scope_type=bundle.scope_type,
        scope_id=bundle.scope_id,
        mission_id=bundle.mission_id,
        kluster_id=bundle.kluster_id,
        version=bundle.version,
        status=bundle.status,
        signature_alg=bundle.signature_alg,
        signing_key_id=bundle.signing_key_id,
        signature=bundle.signature,
        signature_verified=bundle.signature_verified,
        manifest=json.loads(bundle.manifest_json or "{}"),
        sha256=bundle.sha256,
        size_bytes=bundle.size_bytes,
        created_by=bundle.created_by,
        created_at=bundle.created_at,
        updated_at=bundle.updated_at,
    )


@router.post("/missions/{mission_id}/skills/bundles", response_model=SkillBundleRead)
def publish_mission_skill_bundle(mission_id: str, payload: SkillBundleCreate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="skills.bundle.publish",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        assert_mission_writer_or_admin(session=session, request=request, mission_id=mission_id)
        bundle = create_skill_bundle(
            session=session,
            request=request,
            scope_type="mission",
            scope_id=mission_id,
            mission_id=mission_id,
            kluster_id="",
            manifest_payload=payload.manifest,
            tarball_b64=payload.tarball_b64,
            status=payload.status,
            signature_alg=payload.signature_alg,
            signing_key_id=payload.signing_key_id,
            signature=payload.signature,
        )
        enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=None,
            entity_type="skill_bundle",
            entity_id=bundle.id,
            action="publish",
            before=None,
            after={
                "scope_type": "mission",
                "scope_id": mission_id,
                "version": bundle.version,
                "sha256": bundle.sha256,
            },
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        return _bundle_read_payload(bundle)


@router.post("/missions/{mission_id}/klusters/{kluster_id}/skills/bundles", response_model=SkillBundleRead)
def publish_kluster_skill_bundle(mission_id: str, kluster_id: str, payload: SkillBundleCreate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="skills.bundle.publish",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        assert_mission_writer_or_admin(session=session, request=request, mission_id=mission_id)
        validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
        bundle = create_skill_bundle(
            session=session,
            request=request,
            scope_type="kluster",
            scope_id=kluster_id,
            mission_id=mission_id,
            kluster_id=kluster_id,
            manifest_payload=payload.manifest,
            tarball_b64=payload.tarball_b64,
            status=payload.status,
            signature_alg=payload.signature_alg,
            signing_key_id=payload.signing_key_id,
            signature=payload.signature,
        )
        enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=kluster_id,
            entity_type="skill_bundle",
            entity_id=bundle.id,
            action="publish",
            before=None,
            after={
                "scope_type": "kluster",
                "scope_id": kluster_id,
                "version": bundle.version,
                "sha256": bundle.sha256,
            },
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        return _bundle_read_payload(bundle)


@router.get("/skills/snapshots/resolve", response_model=SkillSnapshotResolveRead)
def resolve_snapshot(request: Request, mission_id: str, kluster_id: str = ""):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="skills.snapshot.resolve",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        if kluster_id:
            validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
        snapshot = resolve_effective_snapshot(session=session, mission_id=mission_id, kluster_id=kluster_id)
        enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=kluster_id or None,
            entity_type="skill_snapshot",
            entity_id=snapshot.id,
            action="resolve",
            before=None,
            after={
                "mission_bundle_id": snapshot.mission_bundle_id,
                "kluster_bundle_id": snapshot.kluster_bundle_id,
                "effective_version": snapshot.effective_version,
                "sha256": snapshot.sha256,
            },
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        return SkillSnapshotResolveRead(
            snapshot_id=snapshot.id,
            mission_id=snapshot.mission_id,
            kluster_id=snapshot.kluster_id,
            effective_version=snapshot.effective_version,
            sha256=snapshot.sha256,
            size_bytes=snapshot.size_bytes,
            mission_bundle_id=snapshot.mission_bundle_id,
            kluster_bundle_id=snapshot.kluster_bundle_id,
            manifest=json.loads(snapshot.manifest_json or "{}"),
        )


@router.get("/skills/snapshots/{snapshot_id}/download", response_model=SkillSnapshotDownloadRead)
def download_snapshot(snapshot_id: str, request: Request):
    with get_session() as session:
        snapshot = session.get(SkillSnapshot, snapshot_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="Skill snapshot not found")
        assert_mission_reader_or_admin(session=session, request=request, mission_id=snapshot.mission_id)
        return SkillSnapshotDownloadRead(
            snapshot_id=snapshot.id,
            sha256=snapshot.sha256,
            tarball_b64=snapshot.tarball_b64,
            size_bytes=snapshot.size_bytes,
            manifest=json.loads(snapshot.manifest_json or "{}"),
        )


@router.get("/skills/sync/status", response_model=SkillSyncStatusRead)
def skill_sync_status(request: Request, mission_id: str, kluster_id: str = "", agent_id: str = ""):
    with get_session() as session:
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        if kluster_id:
            validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
        actor = actor_subject_from_request(request)
        state = get_sync_state(
            session=session,
            actor_subject=actor,
            mission_id=mission_id,
            kluster_id=kluster_id,
            agent_id=agent_id,
        )
        if state is None:
            return SkillSyncStatusRead(
                mission_id=mission_id,
                kluster_id=kluster_id,
                actor_subject=actor,
                agent_id=agent_id,
                last_snapshot_id="",
                last_snapshot_sha256="",
                local_overlay_sha256="",
                degraded_offline=False,
                drift_flag=False,
                drift_details={},
                last_sync_at=None,
                updated_at=None,
            )
        return SkillSyncStatusRead(
            mission_id=state.mission_id,
            kluster_id=state.kluster_id,
            actor_subject=state.actor_subject,
            agent_id=state.agent_id,
            last_snapshot_id=state.last_snapshot_id,
            last_snapshot_sha256=state.last_snapshot_sha256,
            local_overlay_sha256=state.local_overlay_sha256,
            degraded_offline=state.degraded_offline,
            drift_flag=state.drift_flag,
            drift_details=json.loads(state.drift_details_json or "{}"),
            last_sync_at=state.last_sync_at,
            updated_at=state.updated_at,
        )


@router.post("/skills/sync/ack", response_model=SkillSyncStatusRead)
def skill_sync_ack(payload: SkillSyncAck, request: Request):
    with get_session() as session:
        assert_mission_reader_or_admin(session=session, request=request, mission_id=payload.mission_id)
        if payload.kluster_id:
            validate_kluster_scope(session=session, mission_id=payload.mission_id, kluster_id=payload.kluster_id)
        actor = actor_subject_from_request(request)
        state = upsert_sync_state(
            session=session,
            actor_subject=actor,
            mission_id=payload.mission_id,
            kluster_id=payload.kluster_id,
            agent_id=payload.agent_id,
            snapshot_id=payload.snapshot_id,
            snapshot_sha256=payload.snapshot_sha256,
            local_overlay_sha256=payload.local_overlay_sha256,
            degraded_offline=payload.degraded_offline,
            drift_flag=payload.drift_flag,
            drift_details=payload.drift_details,
        )
        return SkillSyncStatusRead(
            mission_id=state.mission_id,
            kluster_id=state.kluster_id,
            actor_subject=state.actor_subject,
            agent_id=state.agent_id,
            last_snapshot_id=state.last_snapshot_id,
            last_snapshot_sha256=state.last_snapshot_sha256,
            local_overlay_sha256=state.local_overlay_sha256,
            degraded_offline=state.degraded_offline,
            drift_flag=state.drift_flag,
            drift_details=json.loads(state.drift_details_json or "{}"),
            last_sync_at=state.last_sync_at,
            updated_at=state.updated_at,
        )


@router.post("/missions/{mission_id}/skills/bundles/{bundle_id}/deprecate", response_model=SkillBundleRead)
def deprecate_bundle(mission_id: str, bundle_id: str, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="skills.bundle.deprecate",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        bundle = session.get(SkillBundle, bundle_id)
        if not bundle or bundle.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Skill bundle not found")
        bundle.status = "deprecated"
        bundle.updated_at = datetime.utcnow()
        session.add(bundle)
        session.commit()
        session.refresh(bundle)
        enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=bundle.kluster_id or None,
            entity_type="skill_bundle",
            entity_id=bundle.id,
            action="deprecate",
            before=None,
            after={"status": "deprecated", "version": bundle.version},
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        return _bundle_read_payload(bundle)
