"""
Mission pack service — export and install portable mission bundles.
A mission pack bundles: mission spec, klusters, skill bundles, budget policies, readme.
"""
import base64
import hashlib
import io
import json
import tarfile
import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select
from app.db import engine
from app.models import (
    Mission, Kluster, SkillBundle, BudgetPolicy, MissionPack
)


def pack_mission(mission_id: str, owner_subject: str) -> MissionPack:
    """
    Export a mission and its klusters/skills/budgets into a MissionPack tarball.
    """
    with Session(engine) as session:
        mission = session.exec(
            select(Mission).where(Mission.id == mission_id)
        ).first()
        if not mission:
            raise ValueError(f"Mission {mission_id} not found")

        # Gather klusters linked to this mission
        klusters = list(session.exec(
            select(Kluster).where(Kluster.mission_id == mission_id)
        ).all())

        # Gather skill bundles scoped to this mission
        skill_bundles = list(session.exec(
            select(SkillBundle).where(
                SkillBundle.scope_type == "mission",
                SkillBundle.scope_id == mission_id,
            )
        ).all())

        # Gather budget policies for this mission
        budget_policies = list(session.exec(
            select(BudgetPolicy).where(
                BudgetPolicy.scope_type == "mission",
                BudgetPolicy.scope_id == mission_id,
                BudgetPolicy.owner_subject == owner_subject,
                BudgetPolicy.active == True,
            )
        ).all())

        # Build tarball
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            def add_json(name: str, data: dict):
                content = json.dumps(data, default=str, indent=2).encode()
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))

            add_json("mission.json", {
                "id": mission.id,
                "name": mission.name,
                "description": mission.description,
            })

            for k in klusters:
                add_json(f"klusters/{k.id}.json", {
                    "id": k.id,
                    "name": k.name,
                    "description": k.description,
                })

            for sb in skill_bundles:
                add_json(f"skills/{sb.id}.json", {
                    "id": sb.id,
                    "name": getattr(sb, 'name', sb.id),
                    "version": sb.version,
                    "tarball_b64": sb.tarball_b64,
                    "sha256": sb.sha256,
                })

            for bp in budget_policies:
                add_json(f"budgets/{bp.id}.json", {
                    "scope_type": bp.scope_type,
                    "window_type": bp.window_type,
                    "hard_cap_cents": bp.hard_cap_cents,
                    "soft_cap_cents": bp.soft_cap_cents,
                    "action_on_breach": bp.action_on_breach,
                })

            manifest = {
                "version": 1,
                "mission_id": mission.id,
                "mission_name": mission.name,
                "kluster_count": len(klusters),
                "skill_count": len(skill_bundles),
                "budget_count": len(budget_policies),
                "exported_at": datetime.utcnow().isoformat(),
            }
            add_json("manifest.json", manifest)

        tarball_bytes = buf.getvalue()
        tarball_b64 = base64.b64encode(tarball_bytes).decode()
        sha256 = hashlib.sha256(tarball_bytes).hexdigest()

        pack = MissionPack(
            id=str(uuid.uuid4()),
            owner_subject=owner_subject,
            name=mission.name,
            version=1,
            sha256=sha256,
            tarball_b64=tarball_b64,
            manifest_json=json.dumps(manifest),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(pack)
        session.commit()
        session.refresh(pack)
        return pack


def install_mission_pack(pack_id: str, owner_subject: str, target_mission_id: Optional[str] = None) -> dict:
    """
    Install a mission pack. Creates mission + klusters + skills + budgets.
    Idempotent: if target_mission_id provided, reuses that mission.
    """
    with Session(engine) as session:
        pack = session.exec(
            select(MissionPack).where(MissionPack.id == pack_id, MissionPack.owner_subject == owner_subject)
        ).first()
        if not pack:
            raise ValueError(f"Pack {pack_id} not found")

        tarball_bytes = base64.b64decode(pack.tarball_b64)
        buf = io.BytesIO(tarball_bytes)

        created = {"missions": [], "klusters": [], "skills": [], "budgets": []}

        with tarfile.open(fileobj=buf, mode='r:gz') as tar:
            manifest_file = tar.extractfile("manifest.json")
            manifest = json.loads(manifest_file.read())

            mission_file = tar.extractfile("mission.json")
            mission_spec = json.loads(mission_file.read())

            if target_mission_id:
                mission = session.get(Mission, target_mission_id)
                if not mission:
                    raise ValueError(f"Target mission {target_mission_id} not found")
            else:
                mission = Mission(
                    id=str(uuid.uuid4()),
                    owners=owner_subject,
                    name=f"{mission_spec['name']} (from pack)",
                    description=mission_spec.get('description', ''),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(mission)
                session.flush()
                created["missions"].append(mission.id)

            for member in tar.getmembers():
                if member.name.startswith("klusters/") and member.name.endswith(".json"):
                    f = tar.extractfile(member)
                    k_spec = json.loads(f.read())
                    existing = session.exec(
                        select(Kluster).where(
                            Kluster.mission_id == mission.id,
                            Kluster.name == k_spec["name"],
                        )
                    ).first()
                    if not existing:
                        k = Kluster(
                            id=str(uuid.uuid4()),
                            mission_id=mission.id,
                            name=k_spec["name"],
                            description=k_spec.get("description", ""),
                            owners=owner_subject,
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow(),
                        )
                        session.add(k)
                        created["klusters"].append(k.id)

            for member in tar.getmembers():
                if member.name.startswith("skills/") and member.name.endswith(".json"):
                    f = tar.extractfile(member)
                    sb_spec = json.loads(f.read())
                    existing = session.exec(
                        select(SkillBundle).where(
                            SkillBundle.scope_type == "mission",
                            SkillBundle.scope_id == mission.id,
                            SkillBundle.sha256 == sb_spec["sha256"],
                        )
                    ).first()
                    if not existing:
                        sb = SkillBundle(
                            id=str(uuid.uuid4()),
                            scope_type="mission",
                            scope_id=mission.id,
                            mission_id=mission.id,
                            version=sb_spec.get("version", 1),
                            tarball_b64=sb_spec["tarball_b64"],
                            sha256=sb_spec["sha256"],
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow(),
                        )
                        session.add(sb)
                        created["skills"].append(sb.id)

            for member in tar.getmembers():
                if member.name.startswith("budgets/") and member.name.endswith(".json"):
                    f = tar.extractfile(member)
                    bp_spec = json.loads(f.read())
                    bp = BudgetPolicy(
                        id=str(uuid.uuid4()),
                        owner_subject=owner_subject,
                        scope_type="mission",
                        scope_id=mission.id,
                        window_type=bp_spec["window_type"],
                        hard_cap_cents=bp_spec["hard_cap_cents"],
                        soft_cap_cents=bp_spec.get("soft_cap_cents"),
                        action_on_breach=bp_spec.get("action_on_breach", "alert_only"),
                        active=True,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    session.add(bp)
                    created["budgets"].append(bp.id)

            session.commit()

        return {
            "pack_id": pack_id,
            "mission_id": mission.id,
            "created": created,
            "manifest": manifest,
        }
