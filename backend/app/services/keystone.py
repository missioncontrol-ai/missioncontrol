from __future__ import annotations

from datetime import datetime

from app.models import Kluster, Mission

MISSION_KEYSTONE_FILENAME = "NORTHSTAR.md"
KLUSTER_KEYSTONE_FILENAME = "WORKSTREAM.md"


def _now() -> datetime:
    return datetime.utcnow()


def render_mission_northstar(mission: Mission, *, actor: str) -> str:
    created_by = actor or "unknown"
    mission_id = mission.id or ""
    return (
        f"# NORTHSTAR: {mission.name}\n\n"
        "## Purpose\n"
        f"- Mission ID: `{mission_id}`\n"
        f"- Description: {mission.description or 'TBD'}\n\n"
        "## Governance\n"
        f"- Owners: {mission.owners or 'TBD'}\n"
        f"- Contributors: {mission.contributors or 'TBD'}\n"
        "- Rules: define mission-wide guardrails here\n"
        "- Allowed Actions: define mission-wide allow/deny here\n\n"
        "## Policy\n"
        "- Enforcement Mode: overlay (set to enforce for strict inheritance)\n"
        "- Policy Refs: list policy docs/ids\n\n"
        "## External Storage\n"
        "- Object Store: MC_OBJECT_STORAGE_* configured endpoint/bucket\n"
        "- Prefix: missions/<mission-id>/...\n"
        "- Credentials: reference secret refs only (no plaintext)\n\n"
        "## Integrations\n"
        "- Connection Refs: add service/tool references\n"
        "- Secrets: infisical://<project>/<path>#<key>\n\n"
        "## Data Sources\n"
        "- DB/API refs and access model\n\n"
        "## Agent Runtime\n"
        "- AGENT.md references and execution constraints\n\n"
        "## Versioning\n"
        f"- Version: 1\n"
        f"- Created By: {created_by}\n"
    )


def render_kluster_workstream(kluster: Kluster, *, actor: str) -> str:
    created_by = actor or "unknown"
    kluster_id = kluster.id or ""
    return (
        f"# WORKSTREAM: {kluster.name}\n\n"
        "## Purpose\n"
        f"- Kluster ID: `{kluster_id}`\n"
        f"- Mission ID: `{kluster.mission_id or ''}`\n"
        f"- Description: {kluster.description or 'TBD'}\n\n"
        "## Governance\n"
        f"- Owners: {kluster.owners or 'TBD'}\n"
        f"- Contributors: {kluster.contributors or 'TBD'}\n"
        "- Rules: add kluster-specific rules\n"
        "- Allowed Actions: add kluster-specific action policy\n\n"
        "## Policy Overlay\n"
        "- Inherits Mission NORTHSTAR policy by default\n"
        "- Override Scope: list explicit deviations\n\n"
        "## External Storage\n"
        "- Object Prefix: missions/<mission-id>/klusters/<kluster-id>/\n"
        "- Credentials: secret refs only (no plaintext)\n\n"
        "## Integrations\n"
        "- Tooling, endpoints, and secret refs\n\n"
        "## Data Sources\n"
        "- DB/API refs for this workstream\n\n"
        "## Agent Runtime\n"
        "- AGENT.md references and required capabilities\n\n"
        "## Versioning\n"
        f"- Version: 1\n"
        f"- Created By: {created_by}\n"
    )


def ensure_mission_northstar(mission: Mission, *, actor: str) -> bool:
    if (mission.northstar_md or "").strip():
        return False
    now = _now()
    mission.northstar_md = render_mission_northstar(mission, actor=actor)
    mission.northstar_version = max(int(mission.northstar_version or 0), 1)
    mission.northstar_created_by = mission.northstar_created_by or actor
    mission.northstar_modified_by = actor
    mission.northstar_created_at = mission.northstar_created_at or now
    mission.northstar_modified_at = now
    return True


def ensure_kluster_workstream(kluster: Kluster, *, actor: str) -> bool:
    if (kluster.workstream_md or "").strip():
        return False
    now = _now()
    kluster.workstream_md = render_kluster_workstream(kluster, actor=actor)
    kluster.workstream_version = max(int(kluster.workstream_version or 0), 1)
    kluster.workstream_created_by = kluster.workstream_created_by or actor
    kluster.workstream_modified_by = actor
    kluster.workstream_created_at = kluster.workstream_created_at or now
    kluster.workstream_modified_at = now
    return True
