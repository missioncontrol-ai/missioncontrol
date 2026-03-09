from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlmodel import select
from app.db import get_session
from app.models import IngestionJob
from app.schemas import IngestionRequest, IngestionJobRead
from app.services.ingestion import run_ingestion, build_stub_doc, serialize_config

router = APIRouter(prefix="/ingest", tags=["ingestion"])


def _run_job(job_id: int):
    with get_session() as session:
        job = session.get(IngestionJob, job_id)
        if not job:
            return
        job.status = "running"
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        session.refresh(job)

        try:
            summary = run_ingestion(job, job.source, {})
            doc = build_stub_doc(job.kluster_id, job.source)
            session.add(doc)
            job.status = "complete"
            job.result_summary = str(summary)
        except Exception as exc:
            job.status = "failed"
            job.logs = f"{job.logs}\n{exc}".strip()

        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()


def _enqueue_job(kluster_id: str, source: str, config: dict, tasks: BackgroundTasks) -> IngestionJob:
    job = IngestionJob(kluster_id=kluster_id, source=source, config=serialize_config(config))
    with get_session() as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        tasks.add_task(_run_job, job.id)
        return job


@router.post("/github", response_model=IngestionJobRead)
def ingest_github(payload: IngestionRequest, tasks: BackgroundTasks):
    return _enqueue_job(payload.kluster_id, "github", payload.config, tasks)


@router.post("/drive", response_model=IngestionJobRead)
def ingest_drive(payload: IngestionRequest, tasks: BackgroundTasks):
    return _enqueue_job(payload.kluster_id, "google_drive", payload.config, tasks)


@router.post("/slack", response_model=IngestionJobRead)
def ingest_slack(payload: IngestionRequest, tasks: BackgroundTasks):
    return _enqueue_job(payload.kluster_id, "slack", payload.config, tasks)


@router.get("/jobs", response_model=list[IngestionJobRead])
def list_jobs(kluster_id: Optional[str] = None):
    with get_session() as session:
        stmt = select(IngestionJob)
        if kluster_id is not None:
            stmt = stmt.where(IngestionJob.kluster_id == kluster_id)
        jobs = session.exec(stmt.order_by(IngestionJob.updated_at.desc())).all()
        return jobs


@router.get("/jobs/{job_id}", response_model=IngestionJobRead)
def get_job(job_id: int):
    with get_session() as session:
        job = session.get(IngestionJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job
