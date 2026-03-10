from contextlib import contextmanager
import os
from urllib.parse import quote_plus

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine


def _build_database_url() -> str:
    explicit_url = os.getenv("DATABASE_URL")
    if explicit_url:
        return explicit_url

    host = os.getenv("POSTGRES_HOST")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    db = os.getenv("POSTGRES_DB")
    port = os.getenv("POSTGRES_PORT", "5432")

    if host and user and password and db:
        safe_password = quote_plus(password)
        return f"postgresql+psycopg://{user}:{safe_password}@{host}:{port}/{db}"

    return "sqlite:///./taskman.db"


DATABASE_URL = _build_database_url()

engine_kwargs = {"echo": False}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update(
        {
            "pool_size": int(os.getenv("DB_POOL_SIZE", "20")),
            "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
            "pool_pre_ping": os.getenv("DB_POOL_PRE_PING", "true").strip().lower() in {"1", "true", "yes", "on"},
            "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "3600")),
        }
    )

engine = create_engine(DATABASE_URL, **engine_kwargs)


def init_db() -> None:
    if DATABASE_URL.startswith("sqlite") or os.getenv("MC_DB_AUTO_CREATE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        SQLModel.metadata.create_all(engine)
    if DATABASE_URL.startswith("sqlite") or os.getenv("MC_DB_RUNTIME_MIGRATIONS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        _migrate_schema()


def _migrate_schema() -> None:
    """Runtime migration for kluster cutover and additive columns."""
    tables = set(inspect(engine).get_table_names())

    with engine.begin() as conn:
        # Rename the core table if we are upgrading from legacy naming.
        if "cluster" in tables and "kluster" not in tables:
            conn.execute(text("ALTER TABLE cluster RENAME TO kluster"))

        for table_name in ("doc", "artifact", "epic", "task", "ingestionjob", "ledgerevent"):
            if table_name not in tables:
                continue
            columns = {c["name"] for c in inspect(engine).get_columns(table_name)}
            if "cluster_id" in columns and "kluster_id" not in columns:
                conn.execute(text(f"ALTER TABLE {table_name} RENAME COLUMN cluster_id TO kluster_id"))

    tables = set(inspect(engine).get_table_names())
    if "kluster" in tables:
        kluster_columns = {c["name"] for c in inspect(engine).get_columns("kluster")}
        if "mission_id" not in kluster_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE kluster ADD COLUMN mission_id TEXT"))
        with engine.begin() as conn:
            if "workstream_md" not in kluster_columns:
                conn.execute(text("ALTER TABLE kluster ADD COLUMN workstream_md TEXT DEFAULT ''"))
            if "workstream_version" not in kluster_columns:
                conn.execute(text("ALTER TABLE kluster ADD COLUMN workstream_version INTEGER DEFAULT 1"))
            if "workstream_created_by" not in kluster_columns:
                conn.execute(text("ALTER TABLE kluster ADD COLUMN workstream_created_by TEXT DEFAULT ''"))
            if "workstream_modified_by" not in kluster_columns:
                conn.execute(text("ALTER TABLE kluster ADD COLUMN workstream_modified_by TEXT DEFAULT ''"))
            if "workstream_created_at" not in kluster_columns:
                conn.execute(text("ALTER TABLE kluster ADD COLUMN workstream_created_at TIMESTAMP"))
            if "workstream_modified_at" not in kluster_columns:
                conn.execute(text("ALTER TABLE kluster ADD COLUMN workstream_modified_at TIMESTAMP"))

    if "mission" in tables:
        mission_columns = {c["name"] for c in inspect(engine).get_columns("mission")}
        with engine.begin() as conn:
            if "northstar_md" not in mission_columns:
                conn.execute(text("ALTER TABLE mission ADD COLUMN northstar_md TEXT DEFAULT ''"))
            if "northstar_version" not in mission_columns:
                conn.execute(text("ALTER TABLE mission ADD COLUMN northstar_version INTEGER DEFAULT 1"))
            if "northstar_created_by" not in mission_columns:
                conn.execute(text("ALTER TABLE mission ADD COLUMN northstar_created_by TEXT DEFAULT ''"))
            if "northstar_modified_by" not in mission_columns:
                conn.execute(text("ALTER TABLE mission ADD COLUMN northstar_modified_by TEXT DEFAULT ''"))
            if "northstar_created_at" not in mission_columns:
                conn.execute(text("ALTER TABLE mission ADD COLUMN northstar_created_at TIMESTAMP"))
            if "northstar_modified_at" not in mission_columns:
                conn.execute(text("ALTER TABLE mission ADD COLUMN northstar_modified_at TIMESTAMP"))

    if "agentmessage" not in tables:
        message_columns = set()
    else:
        message_columns = {c["name"] for c in inspect(engine).get_columns("agentmessage")}
    if "slackchannelbinding" in tables:
        binding_columns = {c["name"] for c in inspect(engine).get_columns("slackchannelbinding")}
        with engine.begin() as conn:
            if "provider" not in binding_columns:
                conn.execute(text("ALTER TABLE slackchannelbinding ADD COLUMN provider TEXT DEFAULT 'slack'"))
            if "workspace_external_id" not in binding_columns:
                conn.execute(text("ALTER TABLE slackchannelbinding ADD COLUMN workspace_external_id TEXT DEFAULT ''"))
            if "channel_metadata_json" not in binding_columns:
                conn.execute(text("ALTER TABLE slackchannelbinding ADD COLUMN channel_metadata_json TEXT DEFAULT ''"))

    if message_columns:
        if "message_type" not in message_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agentmessage ADD COLUMN message_type TEXT"))
        if "read" not in message_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agentmessage ADD COLUMN read BOOLEAN DEFAULT 0"))
        if "task_id" not in message_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agentmessage ADD COLUMN task_id INTEGER"))

    if "skillbundle" in tables:
        skillbundle_columns = {c["name"] for c in inspect(engine).get_columns("skillbundle")}
        with engine.begin() as conn:
            if "signature_alg" not in skillbundle_columns:
                conn.execute(text("ALTER TABLE skillbundle ADD COLUMN signature_alg TEXT DEFAULT ''"))
            if "signing_key_id" not in skillbundle_columns:
                conn.execute(text("ALTER TABLE skillbundle ADD COLUMN signing_key_id TEXT DEFAULT ''"))
            if "signature" not in skillbundle_columns:
                conn.execute(text("ALTER TABLE skillbundle ADD COLUMN signature TEXT DEFAULT ''"))
            if "signature_verified" not in skillbundle_columns:
                conn.execute(text("ALTER TABLE skillbundle ADD COLUMN signature_verified BOOLEAN DEFAULT 0"))

    if "task" in tables:
        task_columns = {c["name"] for c in inspect(engine).get_columns("task")}
        with engine.begin() as conn:
            if "public_id" not in task_columns:
                conn.execute(text("ALTER TABLE task ADD COLUMN public_id TEXT DEFAULT ''"))

    if "artifact" in tables:
        artifact_columns = {c["name"] for c in inspect(engine).get_columns("artifact")}
        with engine.begin() as conn:
            if "storage_backend" not in artifact_columns:
                conn.execute(text("ALTER TABLE artifact ADD COLUMN storage_backend TEXT DEFAULT 'inline'"))
            if "content_sha256" not in artifact_columns:
                conn.execute(text("ALTER TABLE artifact ADD COLUMN content_sha256 TEXT DEFAULT ''"))
            if "size_bytes" not in artifact_columns:
                conn.execute(text("ALTER TABLE artifact ADD COLUMN size_bytes INTEGER DEFAULT 0"))
            if "mime_type" not in artifact_columns:
                conn.execute(text("ALTER TABLE artifact ADD COLUMN mime_type TEXT DEFAULT ''"))

    _ensure_owner_constraints_postgres()


def _ensure_owner_constraints_postgres() -> None:
    if engine.dialect.name != "postgresql":
        return

    def _constraint_exists(conn, name: str) -> bool:
        row = conn.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = :name LIMIT 1"),
            {"name": name},
        ).first()
        return row is not None

    with engine.begin() as conn:
        # Backfill legacy empty owners so we can add hard DB constraints safely.
        conn.execute(
            text(
                "UPDATE mission "
                "SET owners = 'owner-required@system' "
                "WHERE btrim(COALESCE(owners, '')) = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE kluster "
                "SET owners = 'owner-required@system' "
                "WHERE btrim(COALESCE(owners, '')) = ''"
            )
        )

        if not _constraint_exists(conn, "ck_mission_owners_nonempty"):
            conn.execute(
                text("ALTER TABLE mission ADD CONSTRAINT ck_mission_owners_nonempty CHECK (btrim(owners) <> '')")
            )
        if not _constraint_exists(conn, "ck_kluster_owners_nonempty"):
            conn.execute(
                text("ALTER TABLE kluster ADD CONSTRAINT ck_kluster_owners_nonempty CHECK (btrim(owners) <> '')")
            )


@contextmanager
def get_session():
    with Session(engine) as session:
        yield session
