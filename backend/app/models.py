from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel
from sqlalchemy import CheckConstraint, Column, Text, UniqueConstraint


class Kluster(SQLModel, table=True):
    __table_args__ = (CheckConstraint("trim(owners) <> ''", name="ck_kluster_owners_nonempty"),)
    id: Optional[str] = Field(default=None, primary_key=True)
    mission_id: Optional[str] = Field(default=None, index=True)
    name: str = Field(index=True)
    description: str = ""
    owners: str = ""
    contributors: str = ""
    tags: str = ""
    status: str = "active"
    workstream_md: str = ""
    workstream_version: int = 1
    workstream_created_by: str = ""
    workstream_modified_by: str = ""
    workstream_created_at: Optional[datetime] = None
    workstream_modified_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Mission(SQLModel, table=True):
    __table_args__ = (CheckConstraint("trim(owners) <> ''", name="ck_mission_owners_nonempty"),)
    id: Optional[str] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = ""
    owners: str = ""
    contributors: str = ""
    tags: str = ""
    visibility: str = "internal"
    status: str = "active"
    northstar_md: str = ""
    northstar_version: int = 1
    northstar_created_by: str = ""
    northstar_modified_by: str = ""
    northstar_created_at: Optional[datetime] = None
    northstar_modified_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Doc(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kluster_id: str = Field(index=True)
    title: str
    body: str
    doc_type: str = "narrative"
    status: str = "draft"
    version: int = 1
    provenance: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Artifact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kluster_id: str = Field(index=True)
    name: str
    artifact_type: str = "file"
    uri: str
    storage_backend: str = "inline"
    content_sha256: str = ""
    size_bytes: int = 0
    mime_type: str = ""
    status: str = "draft"
    version: int = 1
    provenance: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Epic(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kluster_id: str = Field(index=True)
    title: str
    description: str = ""
    owner: str = ""
    status: str = "proposed"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default="", index=True)
    kluster_id: str = Field(index=True)
    epic_id: Optional[int] = Field(default=None, index=True)
    title: str
    description: str = ""
    status: str = "proposed"
    owner: str = ""
    contributors: str = ""
    dependencies: str = ""
    definition_of_done: str = ""
    related_artifacts: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OverlapSuggestion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(index=True)
    candidate_task_id: int = Field(index=True)
    similarity_score: float
    evidence: str = ""
    suggested_action: str = "link"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class IngestionJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kluster_id: str = Field(index=True)
    source: str = Field(index=True)
    status: str = "queued"
    config: str = ""
    logs: str = ""
    result_summary: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Agent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    capabilities: str = ""
    status: str = "offline"
    # Keep DB column name "metadata" for compatibility, avoid reserved attribute name.
    agent_metadata: str = Field(default="", sa_column=Column("metadata", Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AgentSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(index=True)
    context: str = ""
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None


class TaskAssignment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(index=True)
    agent_id: int = Field(index=True)
    status: str = "available"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AgentMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    from_agent_id: int = Field(index=True)
    to_agent_id: int = Field(index=True)
    content: str = ""
    message_type: str = "info"
    task_id: Optional[int] = None
    read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LedgerEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: str = Field(index=True, unique=True)
    mission_id: Optional[str] = Field(default=None, index=True)
    kluster_id: Optional[str] = Field(default=None, index=True)
    entity_type: str = Field(index=True)
    entity_id: str = Field(index=True)
    action: str = Field(index=True)
    payload_json: str = ""
    state: str = Field(default="pending", index=True)
    created_by_agent_id: Optional[int] = Field(default=None, index=True)
    created_by_subject: str = ""
    attempt_count: int = 0
    last_error: str = ""
    git_commit: str = ""
    git_path: str = ""
    published_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MissionRoleMembership(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    subject: str = Field(index=True)
    role: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GovernancePolicy(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    version: int = Field(index=True)
    state: str = Field(default="draft", index=True)  # draft|active|archived
    policy_json: str = ""
    change_note: str = ""
    created_by: str = ""
    published_by: str = ""
    published_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GovernancePolicyEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    policy_id: Optional[int] = Field(default=None, index=True)
    version: int = Field(default=0, index=True)
    event_type: str = Field(index=True)  # draft_created|draft_updated|published|rollback|reload
    actor_subject: str = ""
    detail_json: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class ApprovalRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    action: str = Field(index=True)
    channel: str = Field(default="api", index=True)
    reason: str = ""
    target_entity_type: str = ""
    target_entity_id: str = ""
    request_context_json: str = ""
    status: str = Field(default="pending", index=True)  # pending|approved|rejected|executed
    requested_by: str = ""
    approved_by: str = ""
    rejected_by: str = ""
    decision_note: str = ""
    approval_nonce: str = Field(default="", index=True, unique=True)
    approval_expires_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    executed_action: str = ""
    executed_request_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ApprovalNonceUse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nonce: str = Field(index=True, unique=True)
    approval_request_id: Optional[int] = Field(default=None, index=True)
    request_id: str = ""
    action: str = Field(index=True)
    actor_subject: str = ""
    consumed_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class SlackChannelBinding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(default="slack", index=True)
    mission_id: str = Field(index=True)
    workspace_external_id: str = Field(default="", index=True)
    channel_id: str = Field(index=True)
    channel_name: str = ""
    channel_metadata_json: str = ""
    created_by: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatInboundReceipt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(default="slack", index=True)
    event_key: str = Field(index=True, unique=True)
    event_type: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class SkillBundle(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    scope_type: str = Field(index=True)  # mission|kluster
    scope_id: str = Field(index=True)
    mission_id: str = Field(index=True)
    kluster_id: str = Field(default="", index=True)
    version: int = Field(default=1, index=True)
    status: str = Field(default="active", index=True)  # active|deprecated
    signature_alg: str = Field(default="", index=True)
    signing_key_id: str = Field(default="", index=True)
    signature: str = ""
    signature_verified: bool = Field(default=False, index=True)
    manifest_json: str = ""
    tarball_b64: str = Field(default="", sa_column=Column("tarball_b64", Text))
    sha256: str = Field(default="", index=True)
    size_bytes: int = 0
    created_by: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SkillSnapshot(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    kluster_id: str = Field(default="", index=True)
    mission_bundle_id: str = Field(index=True)
    kluster_bundle_id: str = Field(default="", index=True)
    effective_version: str = Field(index=True)
    manifest_json: str = ""
    tarball_b64: str = Field(default="", sa_column=Column("tarball_b64", Text))
    sha256: str = Field(default="", index=True)
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SkillLocalState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    actor_subject: str = Field(index=True)
    mission_id: str = Field(index=True)
    kluster_id: str = Field(default="", index=True)
    agent_id: str = Field(default="", index=True)
    last_snapshot_id: str = Field(default="", index=True)
    last_snapshot_sha256: str = Field(default="", index=True)
    local_overlay_sha256: str = Field(default="", index=True)
    degraded_offline: bool = Field(default=False, index=True)
    drift_flag: bool = Field(default=False, index=True)
    drift_details_json: str = ""
    last_sync_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FeedbackEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    kluster_id: str = Field(default="", index=True)
    source_type: str = Field(index=True)  # agent|human
    source_subject: str = Field(default="", index=True)
    provider: str = Field(default="", index=True)
    channel_id: str = Field(default="", index=True)
    category: str = Field(default="", index=True)
    severity: str = Field(default="medium", index=True)
    summary: str = ""
    recommendation: str = ""
    status: str = Field(default="open", index=True)
    triage_status: str = Field(default="new", index=True)
    priority: str = Field(default="p2", index=True)
    owner: str = Field(default="", index=True)
    disposition: str = Field(default="", index=True)
    outcome_ref: str = ""
    metadata_json: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserProfile(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("owner_subject", "name", name="uq_userprofile_owner_name"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    owner_subject: str = Field(index=True)
    description: str = ""
    is_default: bool = False
    manifest_json: str = "[]"
    tarball_b64: Optional[str] = Field(default=None, sa_column=Column("tarball_b64", Text))
    sha256: Optional[str] = None
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkspaceLease(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    kluster_id: str = Field(index=True)
    actor_subject: str = Field(index=True)
    agent_id: str = Field(default="", index=True)
    workspace_label: str = Field(default="", index=True)
    status: str = Field(default="active", index=True)  # active|released|expired
    base_snapshot_json: str = ""
    lease_seconds: int = 900
    last_heartbeat_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    expires_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    release_reason: str = ""
    released_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # The subject this session belongs to (email or opaque OIDC subject)
    subject: str = Field(index=True)
    # SHA-256 hex digest of the raw token — stored instead of plaintext
    token_hash: str = Field(unique=True, index=True)
    # First 8 chars of the raw token — for display/debugging only
    token_prefix: str = ""
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: datetime = Field(default_factory=datetime.utcnow)
    # Optional: user-agent string from the client that created the session
    user_agent: str = Field(default="")
    revoked: bool = Field(default=False, index=True)


class EvolveMission(SQLModel, table=True):
    mission_id: str = Field(primary_key=True, index=True)
    owner_subject: str = Field(index=True)
    status: str = Field(default="seeded", index=True)
    spec_json: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class EvolveRun(SQLModel, table=True):
    run_id: str = Field(primary_key=True, index=True)
    mission_id: str = Field(index=True)
    owner_subject: str = Field(index=True)
    agent: str = Field(default="claude", index=True)
    status: str = Field(default="launched", index=True)
    started_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AiSession(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    title: str = ""
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AiTurn(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    role: str = Field(index=True)  # user|assistant|tool
    content_json: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AiEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    turn_id: Optional[int] = Field(default=None, index=True)
    event_type: str = Field(index=True)
    payload_json: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AiPendingAction(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    turn_id: int = Field(index=True)
    tool: str = Field(index=True)
    args_json: str = ""
    reason: str = ""
    status: str = Field(default="pending", index=True)  # pending|approved|rejected|executed
    requested_by: str = ""
    approved_by: str = ""
    rejected_by: str = ""
    rejection_note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class OidcAuthRequest(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    state: str = Field(index=True, unique=True)
    code_verifier: str = ""
    nonce: str = ""
    redirect_path: str = "/ui/"
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    expires_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    used_at: Optional[datetime] = None


class OidcLoginGrant(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    auth_request_id: str = Field(index=True)
    subject: str = Field(index=True)
    email: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    expires_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    used_at: Optional[datetime] = None
