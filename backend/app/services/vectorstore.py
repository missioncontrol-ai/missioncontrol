import json
import os
from pathlib import Path
from typing import List

from sqlalchemy import text

from app.db import DATABASE_URL, engine
from app.services.embeddings import embed_texts

BASE_DIR = Path(__file__).resolve().parents[2]
CHROMA_DIR = BASE_DIR / "chroma"

TASK_COLLECTION = "tasks"
DOC_COLLECTION = "docs"

_VECTOR_BACKEND = os.getenv("VECTOR_STORE_BACKEND", "").strip().lower()
if not _VECTOR_BACKEND:
    _VECTOR_BACKEND = "pgvector" if DATABASE_URL.startswith("postgresql") else "chroma"

_chroma_client = None


def _get_chroma_collection(name: str):
    global _chroma_client
    if _chroma_client is None:
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise RuntimeError(
                "chromadb is not installed. Install it with 'pip install chromadb' "
                "or set VECTOR_STORE_BACKEND=pgvector to use PostgreSQL instead."
            )
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR), settings=Settings(allow_reset=False))
    return _chroma_client.get_or_create_collection(name=name)


def _pgvector_init() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS vector_embedding (
                  collection TEXT NOT NULL,
                  item_id TEXT NOT NULL,
                  embedding vector(384) NOT NULL,
                  document TEXT NOT NULL DEFAULT '',
                  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                  PRIMARY KEY (collection, item_id)
                )
                """
            )
        )
        # ANN indexes keep semantic search responsive as corpus grows.
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_vector_embedding_tasks_hnsw
                ON vector_embedding
                USING hnsw (embedding vector_cosine_ops)
                WHERE collection = 'tasks'
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_vector_embedding_docs_hnsw
                ON vector_embedding
                USING hnsw (embedding vector_cosine_ops)
                WHERE collection = 'docs'
                """
            )
        )


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def _index_pgvector(collection: str, item_id: str, text_value: str, metadata: dict) -> None:
    _pgvector_init()
    embedding = embed_texts([text_value])[0]
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO vector_embedding (collection, item_id, embedding, document, metadata_json)
                VALUES (:collection, :item_id, CAST(:embedding AS vector), :document, CAST(:metadata_json AS jsonb))
                ON CONFLICT (collection, item_id)
                DO UPDATE SET
                  embedding = EXCLUDED.embedding,
                  document = EXCLUDED.document,
                  metadata_json = EXCLUDED.metadata_json
                """
            ),
            {
                "collection": collection,
                "item_id": item_id,
                "embedding": _vector_literal(embedding),
                "document": text_value,
                "metadata_json": json.dumps(metadata),
            },
        )


def _query_pgvector(collection: str, query: str, limit: int) -> List[dict]:
    _pgvector_init()
    embedding = embed_texts([query])[0]
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT item_id, metadata_json, (embedding <=> CAST(:embedding AS vector)) AS distance
                FROM vector_embedding
                WHERE collection = :collection
                ORDER BY embedding <=> CAST(:embedding AS vector) ASC
                LIMIT :limit
                """
            ),
            {
                "collection": collection,
                "embedding": _vector_literal(embedding),
                "limit": max(1, int(limit)),
            },
        ).mappings()
        out: List[dict] = []
        for row in rows:
            metadata_raw = row.get("metadata_json")
            if isinstance(metadata_raw, str):
                try:
                    metadata = json.loads(metadata_raw)
                except json.JSONDecodeError:
                    metadata = {}
            else:
                metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
            out.append(
                {
                    "id": int(row["item_id"]),
                    "distance": float(row["distance"]) if row.get("distance") is not None else None,
                    "metadata": metadata,
                }
            )
        return out


def index_task(task_id: int, text_value: str, metadata: dict):
    if not text_value.strip():
        return
    if _VECTOR_BACKEND == "pgvector":
        _index_pgvector(TASK_COLLECTION, str(task_id), text_value, metadata)
        return
    collection = _get_chroma_collection(TASK_COLLECTION)
    embedding = embed_texts([text_value])[0]
    collection.upsert(
        ids=[str(task_id)],
        embeddings=[embedding],
        documents=[text_value],
        metadatas=[metadata],
    )


def index_doc(doc_id: int, text_value: str, metadata: dict):
    if not text_value.strip():
        return
    if _VECTOR_BACKEND == "pgvector":
        _index_pgvector(DOC_COLLECTION, str(doc_id), text_value, metadata)
        return
    collection = _get_chroma_collection(DOC_COLLECTION)
    embedding = embed_texts([text_value])[0]
    collection.upsert(
        ids=[str(doc_id)],
        embeddings=[embedding],
        documents=[text_value],
        metadatas=[metadata],
    )


def query_tasks(query: str, limit: int = 5) -> List[dict]:
    if _VECTOR_BACKEND == "pgvector":
        return _query_pgvector(TASK_COLLECTION, query, limit)
    collection = _get_chroma_collection(TASK_COLLECTION)
    embeddings = embed_texts([query])
    result = collection.query(query_embeddings=embeddings, n_results=limit)
    return _format_chroma_results(result)


def query_docs(query: str, limit: int = 5) -> List[dict]:
    if _VECTOR_BACKEND == "pgvector":
        return _query_pgvector(DOC_COLLECTION, query, limit)
    collection = _get_chroma_collection(DOC_COLLECTION)
    embeddings = embed_texts([query])
    result = collection.query(query_embeddings=embeddings, n_results=limit)
    return _format_chroma_results(result)


def _format_chroma_results(result) -> List[dict]:
    matches: List[dict] = []
    ids = result.get("ids", [[]])[0]
    scores = result.get("distances", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]

    for idx, item_id in enumerate(ids):
        matches.append(
            {
                "id": int(item_id),
                "distance": float(scores[idx]) if idx < len(scores) else None,
                "metadata": metadatas[idx] if idx < len(metadatas) else {},
            }
        )

    return matches
