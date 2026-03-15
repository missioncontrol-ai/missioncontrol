import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional
from urllib.parse import quote

from sqlmodel import Session, select

from app.models import (
    LedgerEvent,
    MissionPersistencePolicy,
    MissionPersistenceRoute,
    PublicationRecord,
    RepoBinding,
    RepoConnection,
)


class PublishRoutingError(RuntimeError):
    pass


@dataclass
class PublishPlan:
    mission_id: str
    entity_kind: str
    event_kind: str
    binding: RepoBinding
    connection: RepoConnection
    branch: str
    rel_path: str
    format: str


def _run(cmd: list[str], cwd: Optional[Path] = None) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"command failed: {' '.join(cmd)}").strip()
        raise PublishRoutingError(err)
    return proc.stdout or ""


def _resolve_secret_ref(secret_ref: str) -> str:
    value = (secret_ref or "").strip()
    if not value:
        return ""
    if value.startswith("env:"):
        env_name = value.split(":", 1)[1].strip()
        resolved = os.getenv(env_name, "").strip()
        if not resolved:
            raise PublishRoutingError(f"credential env var '{env_name}' is not set")
        return resolved
    if value.startswith("literal:"):
        return value.split(":", 1)[1]
    return value


def _repo_url_with_credentials(connection: RepoConnection) -> str:
    repo_path = (connection.repo_path or "").strip().strip("/")
    if not repo_path:
        raise PublishRoutingError("repo_path is required on repo connection")
    host = (connection.host or "github.com").strip()
    if connection.provider == "ssh":
        return f"git@{host}:{repo_path}.git"
    token = _resolve_secret_ref(connection.credential_ref)
    base = f"https://{host}/{repo_path}.git"
    if not token:
        return base
    username = "x-access-token"
    if connection.provider == "https_token":
        username = "git"
    quoted_user = quote(username, safe="")
    quoted_token = quote(token, safe="")
    return f"https://{quoted_user}:{quoted_token}@{host}/{repo_path}.git"


def _render_rel_path(plan: PublishPlan, *, mission_id: str, entity_id: str) -> str:
    template = plan.rel_path or "missions/{mission_id}/{entity_kind}/{entity_id}.json"
    rel = template.format(
        mission_id=mission_id,
        entity_kind=plan.entity_kind,
        entity_id=entity_id,
        event_kind=plan.event_kind,
    )
    return rel.lstrip("/")


def resolve_publish_plan(
    *,
    session: Session,
    owner_subject: str,
    mission_id: str,
    entity_kind: str,
    event_kind: str,
    entity_id: str,
) -> PublishPlan:
    route = session.exec(
        select(MissionPersistenceRoute)
        .where(MissionPersistenceRoute.mission_id == mission_id)
        .where(MissionPersistenceRoute.entity_kind == entity_kind)
        .where(MissionPersistenceRoute.active == True)  # noqa: E712
        .where((MissionPersistenceRoute.event_kind == event_kind) | (MissionPersistenceRoute.event_kind == ""))
        .order_by(MissionPersistenceRoute.event_kind.desc())
    ).first()
    policy = session.exec(
        select(MissionPersistencePolicy).where(MissionPersistencePolicy.mission_id == mission_id)
    ).first()

    binding_id = route.binding_id if route else (policy.default_binding_id if policy else None)
    if not binding_id:
        raise PublishRoutingError(
            f"no persistence route/default binding for mission '{mission_id}' entity '{entity_kind}' event '{event_kind}'"
        )

    binding = session.get(RepoBinding, binding_id)
    if not binding or not binding.active or binding.owner_subject != owner_subject:
        raise PublishRoutingError(f"binding '{binding_id}' is not available for current principal")
    connection = session.get(RepoConnection, binding.connection_id)
    if not connection or connection.owner_subject != owner_subject:
        raise PublishRoutingError(f"connection '{binding.connection_id}' is not available for current principal")
    branch = (route.branch_override if route and route.branch_override else "") or (
        binding.branch_override or connection.default_branch or "main"
    )
    rel_path = (route.path_template if route else "") or (
        f"{binding.base_path}/{{mission_id}}/{entity_kind}/{{entity_id}}.json"
    )
    return PublishPlan(
        mission_id=mission_id,
        entity_kind=entity_kind,
        event_kind=event_kind,
        binding=binding,
        connection=connection,
        branch=branch,
        rel_path=_render_rel_path(
            PublishPlan(
                mission_id=mission_id,
                entity_kind=entity_kind,
                event_kind=event_kind,
                binding=binding,
                connection=connection,
                branch=branch,
                rel_path=rel_path,
                format=(route.format if route else "json_v1"),
            ),
            mission_id=mission_id,
            entity_id=entity_id,
        ),
        format=(route.format if route else "json_v1"),
    )


def publish_events_with_routing(
    *,
    session: Session,
    owner_subject: str,
    mission_id: str,
    events: list[LedgerEvent],
) -> dict[str, Any]:
    if not events:
        return {"published_count": 0, "commit_sha": "", "branch": "", "repo_url": ""}
    plans: list[tuple[LedgerEvent, PublishPlan]] = []
    for event in events:
        plan = resolve_publish_plan(
            session=session,
            owner_subject=owner_subject,
            mission_id=mission_id,
            entity_kind=event.entity_type,
            event_kind=event.action,
            entity_id=event.entity_id,
        )
        plans.append((event, plan))
    first = plans[0][1]
    same_binding = all(plan.binding.id == first.binding.id for _, plan in plans)
    if not same_binding:
        raise PublishRoutingError("mixed bindings in one publish operation are not yet supported")
    remote_url = _repo_url_with_credentials(first.connection)
    public_repo_url = f"https://{first.connection.host}/{first.connection.repo_path}.git"
    now = datetime.utcnow()
    with TemporaryDirectory(prefix="missioncontrol-routing-publish-") as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        _run(["git", "clone", "--depth", "1", "--single-branch", "--branch", first.branch, remote_url, str(repo_dir)])
        _run(["git", "config", "user.name", "MissionControl Bot"], cwd=repo_dir)
        _run(["git", "config", "user.email", "missioncontrol@local"], cwd=repo_dir)

        for event, plan in plans:
            target = repo_dir / plan.rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = json.loads(event.payload_json or "{}")
            payload["published_at"] = now.isoformat()
            payload["published_by"] = owner_subject
            payload["publication"] = {
                "binding": plan.binding.name,
                "route_entity_kind": plan.entity_kind,
                "route_event_kind": plan.event_kind,
            }
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            _run(["git", "add", plan.rel_path], cwd=repo_dir)

        _run(
            ["git", "commit", "-m", f"publish mission-{mission_id}: {len(plans)} ledger events", "--allow-empty"],
            cwd=repo_dir,
        )
        _run(["git", "push", "origin", first.branch], cwd=repo_dir)
        commit_sha = _run(["git", "rev-parse", "HEAD"], cwd=repo_dir).strip()

    for event, plan in plans:
        event.state = "published"
        event.published_at = now
        event.git_commit = commit_sha
        event.git_path = plan.rel_path
        event.last_error = ""
        event.updated_at = now
        session.add(event)
        record = PublicationRecord(
            owner_subject=owner_subject,
            mission_id=mission_id,
            ledger_event_id=event.id,
            entity_kind=event.entity_type,
            entity_id=event.entity_id,
            event_kind=event.action,
            binding_id=plan.binding.id,
            repo_url=public_repo_url,
            branch=first.branch,
            file_path=plan.rel_path,
            commit_sha=commit_sha,
            status="succeeded",
            detail_json=json.dumps({"format": plan.format}, separators=(",", ":")),
        )
        session.add(record)
    session.commit()
    return {"published_count": len(plans), "commit_sha": commit_sha, "branch": first.branch, "repo_url": public_repo_url}


def get_publication_status(
    *,
    session: Session,
    owner_subject: str,
    mission_id: Optional[str] = None,
    limit: int = 20,
) -> list[PublicationRecord]:
    stmt = select(PublicationRecord).where(PublicationRecord.owner_subject == owner_subject)
    if mission_id:
        stmt = stmt.where(PublicationRecord.mission_id == mission_id)
    stmt = stmt.order_by(PublicationRecord.created_at.desc()).limit(max(1, min(limit, 200)))
    return session.exec(stmt).all()
