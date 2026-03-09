from datetime import datetime
from typing import Dict
from app.models import IngestionJob, Doc


def serialize_config(config: Dict) -> str:
    return str(config)


def run_ingestion(job: IngestionJob, source: str, config: Dict) -> dict:
    """
    Stub ingestion flow. In production this would call source APIs,
    normalize content, and store docs/artifacts.
    """
    summary = {
        "source": source,
        "fetched": 0,
        "created_docs": 1,
        "created_artifacts": 0,
    }
    return summary


def build_stub_doc(kluster_id: str, source: str) -> Doc:
    title = f"Ingested from {source}"
    body = (
        f"Stub ingestion result for {source}. Replace with real connector output."
    )
    return Doc(kluster_id=kluster_id, title=title, body=body, doc_type="ingested", status="draft")
