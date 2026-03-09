import base64
import hashlib
import hmac
import io
import json
import os
import tarfile
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request
from sqlmodel import Session, select

from app.models import Kluster, SkillBundle, SkillLocalState, SkillSnapshot
from app.services.authz import actor_subject_from_request
from app.services.ids import new_hash_id


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canon_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _bundle_signature_payload(*, manifest: dict[str, Any], tarball_sha256: str, signature_alg: str) -> str:
    payload = {
        "manifest": manifest,
        "signature_alg": signature_alg,
        "tarball_sha256": tarball_sha256,
    }
    return _canon_json(payload)


def compute_bundle_signature(*, manifest: dict[str, Any], tarball_sha256: str, secret: str, signature_alg: str) -> str:
    if signature_alg != "hmac-sha256":
        raise HTTPException(status_code=422, detail="Unsupported signature_alg; expected hmac-sha256")
    message = _bundle_signature_payload(
        manifest=manifest,
        tarball_sha256=tarball_sha256,
        signature_alg=signature_alg,
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _decode_tarball_b64(tarball_b64: str) -> bytes:
    try:
        return base64.b64decode(tarball_b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid tarball_b64: {exc}") from exc


def _safe_member_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized or normalized.startswith("../") or "/../" in normalized:
        raise HTTPException(status_code=422, detail=f"Invalid tar member path: {path}")
    return normalized


def extract_tar_entries(tarball_bytes: bytes) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                path = _safe_member_path(member.name)
                handle = tf.extractfile(member)
                if handle is None:
                    continue
                entries[path] = handle.read()
    except tarfile.TarError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid tar.gz bundle: {exc}") from exc
    if not entries:
        raise HTTPException(status_code=422, detail="Skill bundle tarball has no files")
    return entries


def build_normalized_manifest(
    *,
    scope_type: str,
    scope_id: str,
    mission_id: str,
    kluster_id: str,
    manifest_payload: dict[str, Any],
    entries: dict[str, bytes],
) -> dict[str, Any]:
    listed_files = manifest_payload.get("files")
    remove_paths = manifest_payload.get("remove_paths") or []

    if listed_files is None:
        listed_map = {}
    elif isinstance(listed_files, dict):
        listed_map = listed_files
    elif isinstance(listed_files, list):
        listed_map = {}
        for item in listed_files:
            if not isinstance(item, dict) or "path" not in item:
                raise HTTPException(status_code=422, detail="manifest.files list items must include path")
            listed_map[str(item["path"])] = item
    else:
        raise HTTPException(status_code=422, detail="manifest.files must be a map or list")

    normalized_files = []
    for path in sorted(entries.keys()):
        data = entries[path]
        computed_sha = _sha256_hex(data)
        listed = listed_map.get(path) or {}
        listed_sha = listed.get("sha256") if isinstance(listed, dict) else None
        if listed_sha and str(listed_sha) != computed_sha:
            raise HTTPException(status_code=422, detail=f"manifest hash mismatch for path: {path}")
        normalized_files.append(
            {
                "path": path,
                "sha256": computed_sha,
                "size": len(data),
            }
        )

    remove_list: list[str] = []
    if remove_paths:
        if not isinstance(remove_paths, list):
            raise HTTPException(status_code=422, detail="manifest.remove_paths must be a list")
        remove_list = sorted({_safe_member_path(str(p)) for p in remove_paths if str(p).strip()})

    return {
        "format": "mc-skill-bundle/v1",
        "scope_type": scope_type,
        "scope_id": scope_id,
        "mission_id": mission_id,
        "kluster_id": kluster_id,
        "files": normalized_files,
        "remove_paths": remove_list,
    }


def create_skill_bundle(
    *,
    session: Session,
    request: Request,
    scope_type: str,
    scope_id: str,
    mission_id: str,
    kluster_id: str,
    manifest_payload: dict[str, Any],
    tarball_b64: str,
    status: str,
    signature_alg: str = "",
    signing_key_id: str = "",
    signature: str = "",
) -> SkillBundle:
    if scope_type not in {"mission", "kluster"}:
        raise HTTPException(status_code=422, detail="scope_type must be mission or kluster")
    if status not in {"active", "deprecated"}:
        raise HTTPException(status_code=422, detail="status must be active or deprecated")
    signature_alg = (signature_alg or "").strip() or "hmac-sha256"
    signing_key_id = (signing_key_id or "").strip()
    signature = (signature or "").strip().lower()

    tarball_bytes = _decode_tarball_b64(tarball_b64)
    entries = extract_tar_entries(tarball_bytes)
    manifest = build_normalized_manifest(
        scope_type=scope_type,
        scope_id=scope_id,
        mission_id=mission_id,
        kluster_id=kluster_id,
        manifest_payload=manifest_payload or {},
        entries=entries,
    )
    tarball_sha256 = _sha256_hex(tarball_bytes)

    signature_verified = False
    signing_secret = (os.getenv("MC_SKILLS_SIGNING_SECRET") or "").strip()
    if signing_secret:
        if not signature:
            raise HTTPException(status_code=422, detail="signature is required when signing verification is enabled")
        expected = compute_bundle_signature(
            manifest=manifest,
            tarball_sha256=tarball_sha256,
            secret=signing_secret,
            signature_alg=signature_alg,
        )
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=422, detail="Skill bundle signature verification failed")
        signature_verified = True

    if scope_type == "mission":
        q = select(SkillBundle).where(
            SkillBundle.scope_type == "mission",
            SkillBundle.scope_id == scope_id,
        )
    else:
        q = select(SkillBundle).where(
            SkillBundle.scope_type == "kluster",
            SkillBundle.scope_id == scope_id,
        )
    latest = session.exec(q.order_by(SkillBundle.version.desc())).first()
    next_version = (latest.version + 1) if latest else 1

    bundle = SkillBundle(
        id=new_hash_id(),
        scope_type=scope_type,
        scope_id=scope_id,
        mission_id=mission_id,
        kluster_id=kluster_id,
        version=next_version,
        status=status,
        signature_alg=signature_alg,
        signing_key_id=signing_key_id,
        signature=signature,
        signature_verified=signature_verified,
        manifest_json=_canon_json(manifest),
        tarball_b64=base64.b64encode(tarball_bytes).decode("ascii"),
        sha256=tarball_sha256,
        size_bytes=len(tarball_bytes),
        created_by=actor_subject_from_request(request),
    )
    while session.get(SkillBundle, bundle.id):
        bundle.id = new_hash_id()
    session.add(bundle)
    session.commit()
    session.refresh(bundle)
    return bundle


def latest_bundle_for_scope(
    *,
    session: Session,
    scope_type: str,
    scope_id: str,
) -> SkillBundle | None:
    return session.exec(
        select(SkillBundle)
        .where(SkillBundle.scope_type == scope_type)
        .where(SkillBundle.scope_id == scope_id)
        .where(SkillBundle.status == "active")
        .order_by(SkillBundle.version.desc())
    ).first()


def _manifest_files_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in manifest.get("files") or []:
        path = str(item.get("path") or "")
        if not path:
            continue
        out[path] = item
    return out


def _encode_tar_entries(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tf:
        for path in sorted(entries.keys()):
            data = entries[path]
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            tf.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def resolve_effective_snapshot(*, session: Session, mission_id: str, kluster_id: str = "") -> SkillSnapshot:
    mission_bundle = latest_bundle_for_scope(session=session, scope_type="mission", scope_id=mission_id)
    if mission_bundle is None:
        raise HTTPException(status_code=404, detail="No active mission skill bundle found")

    kluster_bundle: SkillBundle | None = None
    if kluster_id:
        kluster_bundle = latest_bundle_for_scope(session=session, scope_type="kluster", scope_id=kluster_id)

    mission_bytes = _decode_tarball_b64(mission_bundle.tarball_b64)
    mission_entries = extract_tar_entries(mission_bytes)
    mission_manifest = json.loads(mission_bundle.manifest_json or "{}")

    effective_entries = dict(mission_entries)
    effective_manifest_files = _manifest_files_map(mission_manifest)

    kluster_manifest: dict[str, Any] = {}
    if kluster_bundle is not None:
        kluster_bytes = _decode_tarball_b64(kluster_bundle.tarball_b64)
        kluster_entries = extract_tar_entries(kluster_bytes)
        kluster_manifest = json.loads(kluster_bundle.manifest_json or "{}")
        for remove_path in kluster_manifest.get("remove_paths") or []:
            remove_path = str(remove_path)
            effective_entries.pop(remove_path, None)
            effective_manifest_files.pop(remove_path, None)
        for path, data in kluster_entries.items():
            effective_entries[path] = data
        for path, meta in _manifest_files_map(kluster_manifest).items():
            effective_manifest_files[path] = meta

    effective_manifest = {
        "format": "mc-skill-snapshot/v1",
        "mission_id": mission_id,
        "kluster_id": kluster_id,
        "source": {
            "mission_bundle_id": mission_bundle.id,
            "kluster_bundle_id": kluster_bundle.id if kluster_bundle else "",
            "mission_bundle_version": mission_bundle.version,
            "kluster_bundle_version": kluster_bundle.version if kluster_bundle else 0,
        },
        "files": [effective_manifest_files[p] for p in sorted(effective_manifest_files.keys())],
    }

    signature_payload = {
        "mission_bundle_sha": mission_bundle.sha256,
        "kluster_bundle_sha": kluster_bundle.sha256 if kluster_bundle else "",
        "manifest": effective_manifest,
    }
    signature = _sha256_hex(_canon_json(signature_payload).encode("utf-8"))

    existing = session.exec(
        select(SkillSnapshot)
        .where(SkillSnapshot.mission_id == mission_id)
        .where(SkillSnapshot.kluster_id == kluster_id)
        .where(SkillSnapshot.sha256 == signature)
        .order_by(SkillSnapshot.created_at.desc())
    ).first()
    if existing:
        return existing

    tar_bytes = _encode_tar_entries(effective_entries)
    effective_version = f"m{mission_bundle.version}-k{kluster_bundle.version if kluster_bundle else 0}"

    snapshot = SkillSnapshot(
        id=new_hash_id(),
        mission_id=mission_id,
        kluster_id=kluster_id,
        mission_bundle_id=mission_bundle.id,
        kluster_bundle_id=kluster_bundle.id if kluster_bundle else "",
        effective_version=effective_version,
        manifest_json=_canon_json(effective_manifest),
        tarball_b64=base64.b64encode(tar_bytes).decode("ascii"),
        sha256=signature,
        size_bytes=len(tar_bytes),
    )
    while session.get(SkillSnapshot, snapshot.id):
        snapshot.id = new_hash_id()
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return snapshot


def get_sync_state(
    *,
    session: Session,
    actor_subject: str,
    mission_id: str,
    kluster_id: str,
    agent_id: str,
) -> SkillLocalState | None:
    return session.exec(
        select(SkillLocalState)
        .where(SkillLocalState.actor_subject == actor_subject)
        .where(SkillLocalState.mission_id == mission_id)
        .where(SkillLocalState.kluster_id == kluster_id)
        .where(SkillLocalState.agent_id == agent_id)
        .order_by(SkillLocalState.updated_at.desc())
    ).first()


def upsert_sync_state(
    *,
    session: Session,
    actor_subject: str,
    mission_id: str,
    kluster_id: str,
    agent_id: str,
    snapshot_id: str,
    snapshot_sha256: str,
    local_overlay_sha256: str,
    degraded_offline: bool,
    drift_flag: bool,
    drift_details: dict[str, Any],
) -> SkillLocalState:
    state = get_sync_state(
        session=session,
        actor_subject=actor_subject,
        mission_id=mission_id,
        kluster_id=kluster_id,
        agent_id=agent_id,
    )
    now = datetime.utcnow()
    if state is None:
        state = SkillLocalState(
            actor_subject=actor_subject,
            mission_id=mission_id,
            kluster_id=kluster_id,
            agent_id=agent_id,
        )
    state.last_snapshot_id = snapshot_id
    state.last_snapshot_sha256 = snapshot_sha256
    state.local_overlay_sha256 = local_overlay_sha256
    state.degraded_offline = degraded_offline
    state.drift_flag = drift_flag
    state.drift_details_json = _canon_json(drift_details or {})
    state.last_sync_at = now
    state.updated_at = now
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def validate_kluster_scope(*, session: Session, mission_id: str, kluster_id: str) -> None:
    if not kluster_id:
        return
    kluster = session.get(Kluster, kluster_id)
    if not kluster or kluster.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="Kluster not found in mission")
