from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from app.db import get_session
from app.models import Kluster, Mission, Task
from app.services.authz import assert_mission_reader_or_admin, is_platform_admin, readable_mission_ids_for_request
from app.services.task_identity import resolve_task_by_ref
from app.schemas import (
    ExplorerKlusterSummary,
    ExplorerMissionSummary,
    ExplorerNodeDetailRead,
    ExplorerTaskSummary,
    ExplorerTreeRead,
)
from app.services.pagination import bounded_limit, limit_query

router = APIRouter(prefix="/explorer", tags=["explorer"])


def _matches_query(needle: str, *values: Optional[str]) -> bool:
    for value in values:
        if value and needle in value.lower():
            return True
    return False


def _to_dict(model_obj):
    if model_obj is None:
        return None
    if hasattr(model_obj, "model_dump"):
        return model_obj.model_dump()
    return dict(model_obj)


def _build_cluster_summary(
    kluster: Kluster,
    tasks: list[Task],
    limit_tasks_per_cluster: int,
) -> ExplorerKlusterSummary:
    status_counts: dict[str, int] = defaultdict(int)
    for task in tasks:
        status_counts[task.status] += 1
    recent_tasks = [
        ExplorerTaskSummary(
            id=task.id,
            kluster_id=task.kluster_id,
            title=task.title,
            status=task.status,
            owner=task.owner,
            updated_at=task.updated_at,
        )
        for task in tasks[:limit_tasks_per_cluster]
    ]
    return ExplorerKlusterSummary(
        id=kluster.id,
        mission_id=kluster.mission_id,
        name=kluster.name,
        description=kluster.description,
        status=kluster.status,
        owners=kluster.owners,
        tags=kluster.tags,
        updated_at=kluster.updated_at,
        task_count=len(tasks),
        task_status_counts=dict(status_counts),
        recent_tasks=recent_tasks,
    )


@router.get("/tree", response_model=ExplorerTreeRead)
def get_explorer_tree(
    mission_id: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit_tasks_per_cluster: int = limit_query(default=5, maximum=50),
    limit_klusters: int = limit_query(default=100, maximum=200),
    request: Request = None,
):
    needle = (q or "").strip().lower()
    cluster_limit = bounded_limit(limit_klusters, default=100, maximum=200)
    with get_session() as session:
        admin = is_platform_admin(request)
        readable_ids = readable_mission_ids_for_request(session=session, request=request) if not admin else None
        if not admin and mission_id is not None and mission_id not in (readable_ids or set()):
            return ExplorerTreeRead(
                generated_at=datetime.utcnow(),
                mission_count=0,
                kluster_count=0,
                task_count=0,
                missions=[],
                unassigned_klusters=[],
            )
        mission_stmt = select(Mission).order_by(Mission.updated_at.desc())
        if mission_id is not None:
            mission_stmt = mission_stmt.where(Mission.id == mission_id)
        missions = session.exec(mission_stmt).all()
        if not admin:
            allowed_ids = readable_ids or set()
            missions = [mission for mission in missions if mission.id in allowed_ids]

        cluster_stmt = select(Kluster).order_by(Kluster.updated_at.desc())
        if mission_id is not None:
            cluster_stmt = cluster_stmt.where(Kluster.mission_id == mission_id)
        klusters = session.exec(cluster_stmt.limit(cluster_limit)).all()
        if not admin:
            allowed_ids = readable_ids or set()
            klusters = [kluster for kluster in klusters if kluster.mission_id in allowed_ids]

        cluster_ids = [kluster.id for kluster in klusters]
        if cluster_ids:
            task_stmt = select(Task).where(Task.kluster_id.in_(cluster_ids)).order_by(Task.updated_at.desc())
            if status is not None:
                task_stmt = task_stmt.where(Task.status == status)
            tasks = session.exec(task_stmt).all()
        else:
            tasks = []

    tasks_by_cluster: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        tasks_by_cluster[task.kluster_id].append(task)

    clusters_by_mission: dict[Optional[str], list[Kluster]] = defaultdict(list)
    for kluster in klusters:
        clusters_by_mission[kluster.mission_id].append(kluster)

    mission_summaries: list[ExplorerMissionSummary] = []
    included_cluster_ids: set[str] = set()

    for mission in missions:
        mission_match = _matches_query(
            needle,
            mission.name,
            mission.description,
            mission.tags,
            mission.owners,
        ) if needle else True
        cluster_summaries: list[ExplorerKlusterSummary] = []
        for kluster in clusters_by_mission.get(mission.id, []):
            all_cluster_tasks = tasks_by_cluster.get(kluster.id, [])
            cluster_match = _matches_query(
                needle,
                kluster.name,
                kluster.description,
                kluster.tags,
                kluster.owners,
            ) if needle else True
            matching_tasks = [
                task
                for task in all_cluster_tasks
                if _matches_query(needle, task.title, task.description, task.owner, task.status)
            ] if needle else all_cluster_tasks
            if needle and not mission_match and not cluster_match and not matching_tasks:
                continue
            display_tasks = all_cluster_tasks if (mission_match or cluster_match) else matching_tasks
            cluster_summaries.append(
                _build_cluster_summary(
                    kluster=kluster,
                    tasks=display_tasks,
                    limit_tasks_per_cluster=limit_tasks_per_cluster,
                )
            )
            included_cluster_ids.add(kluster.id)

        if needle and not mission_match and not cluster_summaries:
            continue

        mission_summaries.append(
            ExplorerMissionSummary(
                id=mission.id,
                name=mission.name,
                description=mission.description,
                status=mission.status,
                visibility=mission.visibility,
                owners=mission.owners,
                tags=mission.tags,
                updated_at=mission.updated_at,
                kluster_count=len(cluster_summaries),
                task_count=sum(c.task_count for c in cluster_summaries),
                klusters=cluster_summaries,
            )
        )

    unassigned_summaries: list[ExplorerKlusterSummary] = []
    for kluster in clusters_by_mission.get(None, []):
        all_cluster_tasks = tasks_by_cluster.get(kluster.id, [])
        cluster_match = _matches_query(
            needle,
            kluster.name,
            kluster.description,
            kluster.tags,
            kluster.owners,
        ) if needle else True
        matching_tasks = [
            task
            for task in all_cluster_tasks
            if _matches_query(needle, task.title, task.description, task.owner, task.status)
        ] if needle else all_cluster_tasks
        if needle and not cluster_match and not matching_tasks:
            continue
        display_tasks = all_cluster_tasks if cluster_match else matching_tasks
        unassigned_summaries.append(
            _build_cluster_summary(
                kluster=kluster,
                tasks=display_tasks,
                limit_tasks_per_cluster=limit_tasks_per_cluster,
            )
        )
        included_cluster_ids.add(kluster.id)

    included_tasks = 0
    for mission in mission_summaries:
        included_tasks += mission.task_count
    for kluster in unassigned_summaries:
        included_tasks += kluster.task_count

    return ExplorerTreeRead(
        generated_at=datetime.utcnow(),
        mission_count=len(mission_summaries),
        kluster_count=len(included_cluster_ids),
        task_count=included_tasks,
        missions=mission_summaries,
        unassigned_klusters=unassigned_summaries,
    )


@router.get("/node/{node_type}/{node_id}", response_model=ExplorerNodeDetailRead)
def get_explorer_node(
    node_type: str,
    node_id: str,
    limit_tasks: int = limit_query(default=50, maximum=200),
    request: Request = None,
):
    with get_session() as session:
        if node_type == "mission":
            mission = session.get(Mission, node_id)
            if not mission:
                raise HTTPException(status_code=404, detail="Mission not found")
            assert_mission_reader_or_admin(session=session, request=request, mission_id=node_id)
            klusters = session.exec(
                select(Kluster)
                .where(Kluster.mission_id == node_id)
                .order_by(Kluster.updated_at.desc())
            ).all()
            cluster_ids = [kluster.id for kluster in klusters]
            task_stmt = select(Task).order_by(Task.updated_at.desc())
            if cluster_ids:
                task_stmt = task_stmt.where(Task.kluster_id.in_(cluster_ids))
            tasks = session.exec(task_stmt.limit(limit_tasks)).all()
            return ExplorerNodeDetailRead(
                node_type=node_type,
                node_id=node_id,
                mission=_to_dict(mission),
                klusters=[_to_dict(kluster) for kluster in klusters],
                tasks=[_to_dict(task) for task in tasks],
            )

        if node_type == "kluster":
            kluster = session.get(Kluster, node_id)
            if not kluster:
                raise HTTPException(status_code=404, detail="Kluster not found")
            if kluster.mission_id:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            elif not is_platform_admin(request):
                raise HTTPException(status_code=403, detail="Forbidden: mission viewer, contributor, or owner required")
            mission = session.get(Mission, kluster.mission_id) if kluster.mission_id else None
            tasks = session.exec(
                select(Task)
                .where(Task.kluster_id == node_id)
                .order_by(Task.updated_at.desc())
                .limit(limit_tasks)
            ).all()
            return ExplorerNodeDetailRead(
                node_type=node_type,
                node_id=node_id,
                mission=_to_dict(mission),
                kluster=_to_dict(kluster),
                tasks=[_to_dict(task) for task in tasks],
            )

        if node_type == "task":
            task = resolve_task_by_ref(session=session, task_ref=node_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            kluster = session.get(Kluster, task.kluster_id)
            if not kluster:
                raise HTTPException(status_code=404, detail="Kluster not found")
            if kluster.mission_id:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            elif not is_platform_admin(request):
                raise HTTPException(status_code=403, detail="Forbidden: mission viewer, contributor, or owner required")
            mission = session.get(Mission, kluster.mission_id) if kluster and kluster.mission_id else None
            return ExplorerNodeDetailRead(
                node_type=node_type,
                node_id=node_id,
                mission=_to_dict(mission),
                kluster=_to_dict(kluster),
                task=_to_dict(task),
            )

    raise HTTPException(status_code=400, detail="node_type must be one of: mission, kluster, task")
