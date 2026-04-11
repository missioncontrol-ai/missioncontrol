from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Text, UniqueConstraint


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
    visibility: str = "public"
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
    storage_class: str = ""
    content_b64: Optional[str] = Field(default=None, sa_column=Column("content_b64", Text))
    external_pointer: bool = False
    external_uri: str = ""
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
    claude_session_id: Optional[str] = Field(default=None, index=True)
    end_reason: Optional[str] = None
    audit_log: str = Field(default="", sa_column=Column("audit_log", Text))


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


class RepoConnection(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("owner_subject", "name", name="uq_repo_connection_owner_name"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    name: str = Field(index=True)
    provider: str = Field(index=True)  # github_app|ssh|https_token
    host: str = Field(default="github.com", index=True)
    repo_path: str = Field(default="")  # owner/repo
    default_branch: str = Field(default="main")
    credential_ref: str = Field(default="")
    options_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RepoBinding(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("owner_subject", "name", name="uq_repo_binding_owner_name"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    name: str = Field(index=True)
    connection_id: int = Field(index=True)
    branch_override: str = Field(default="")
    base_path: str = Field(default="missions")
    active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MissionPersistencePolicy(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("mission_id", name="uq_mission_persistence_policy_mission"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    default_binding_id: Optional[int] = Field(default=None, index=True)
    fallback_mode: str = Field(default="fail_closed", index=True)
    require_approval: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MissionPersistenceRoute(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    entity_kind: str = Field(index=True)  # artifact|doc|mission_snapshot
    event_kind: str = Field(default="", index=True)
    binding_id: int = Field(index=True)
    branch_override: str = Field(default="")
    path_template: str = Field(default="missions/{mission_id}/{entity_kind}/{entity_id}.json")
    format: str = Field(default="json_v1")
    active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PublicationRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    mission_id: Optional[str] = Field(default=None, index=True)
    ledger_event_id: Optional[int] = Field(default=None, index=True)
    entity_kind: str = Field(index=True)
    entity_id: str = Field(index=True)
    event_kind: str = Field(index=True)
    binding_id: int = Field(index=True)
    repo_url: str = Field(default="")
    branch: str = Field(default="")
    file_path: str = Field(default="")
    commit_sha: str = Field(default="", index=True)
    status: str = Field(default="succeeded", index=True)  # planned|succeeded|failed
    error: str = Field(default="")
    detail_json: str = Field(default="{}")
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
    mirror_uri: str = ""
    mirror_sha256: str = ""
    mirror_size_bytes: int = 0
    mirrored_at: Optional[datetime] = None
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
    # Optional: comma-separated capability restrictions for scoped tokens
    capability_scope: str = Field(default="", sa_column=Column("capability_scope", Text))


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
    ai_session_id: Optional[str] = Field(default=None, index=True)


class AiSession(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    title: str = ""
    status: str = Field(default="active", index=True)
    # Runtime layer fields (added in migration 20260315_0010)
    runtime_kind: str = Field(default="opencode", index=True)
    runtime_session_id: Optional[str] = Field(default=None)   # ID in the runtime service
    workspace_path: Optional[str] = Field(default=None)
    policy_json: str = Field(default="{}", sa_column=Column(Text))
    capability_snapshot_json: str = Field(default="{}", sa_column=Column(Text))
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
    cli_nonce: Optional[str] = Field(default=None, index=True)  # set for CLI login flows
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    expires_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    used_at: Optional[datetime] = None


class OidcLoginGrant(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    auth_request_id: str = Field(index=True)
    subject: str = Field(index=True)
    email: str = Field(default="", index=True)
    cli_nonce: Optional[str] = Field(default=None, index=True)  # propagated from OidcAuthRequest
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    expires_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    used_at: Optional[datetime] = None


class ScheduledAgentJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    name: str
    description: str = ""
    cron_expr: str                                    # "0 8 * * *" — standard cron
    runtime_kind: str = "claude_code"
    initial_prompt: str = Field(sa_column=Column(Text))
    system_context: Optional[str] = Field(default=None, sa_column=Column(Text))
    policy_json: str = Field(default="{}", sa_column=Column(Text))
    enabled: bool = True
    last_run_at: Optional[datetime] = Field(default=None)
    last_session_id: Optional[str] = Field(default=None)
    target_type: Optional[str] = Field(default="ai_session")
    target_spec_json: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class EventTrigger(SQLModel, table=True):
    """Fires when a mesh event matches event_type and optional predicate."""
    id: Optional[str] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    event_type: str  # task_completed|task_failed|artifact_published|run_completed|custom
    predicate_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    target_type: str = Field(default="mesh_task")  # mesh_task|ai_session
    target_spec_json: str = Field(sa_column=Column(Text))
    active: bool = True
    cooldown_seconds: int = 0
    last_fired_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RemoteTarget(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("owner_subject", "name", name="uq_remotetarget_owner_name"),)
    id: str = Field(primary_key=True)
    owner_subject: str = Field(index=True)
    name: str = Field(index=True)
    host: str
    user: str = Field(default="")
    port: int = Field(default=22)
    transport: str = Field(default="ssh")   # "ssh" | "k8s"
    ssh_pubkey: str = Field(default="", sa_column=Column(Text))
    key_fingerprint: str = Field(default="")
    last_used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RemoteLaunchRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    owner_subject: str = Field(index=True)
    transport: str                               # "ssh" | "k8s"
    target_id: Optional[str] = Field(default=None, index=True)
    target_host: str = Field(default="")
    target_namespace: str = Field(default="")
    agent_kind: str
    agent_profile: str = Field(default="")
    runtime_session_id: str = Field(default="", index=True)
    session_token_id: Optional[int] = Field(default=None)
    capability_scope: str = Field(default="", sa_column=Column(Text))
    status: str = Field(default="launching", index=True)
    # "launching" | "running" | "heartbeat_lost" | "completed" | "failed"
    last_heartbeat_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_message: str = ""
    log_tail: str = Field(default="", sa_column=Column(Text))
    mc_binary_path: str = Field(default="")
    agent_binary_path: str = Field(default="")
    k8s_job_name: str = Field(default="")
    mc_version: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RuntimeNode(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("node_name", name="uq_runtimenode_name"),)
    id: str = Field(primary_key=True)
    owner_subject: str = Field(index=True)
    node_name: str = Field(index=True)
    hostname: str = Field(default="")
    status: str = Field(default="offline", index=True)
    trust_tier: str = Field(default="untrusted")
    labels_json: str = Field(default="{}", sa_column=Column(Text))
    capacity_json: str = Field(default="{}", sa_column=Column(Text))
    capabilities_json: str = Field(default="[]", sa_column=Column(Text))
    runtime_version: str = Field(default="")
    bootstrap_token_prefix: str = Field(default="")
    last_heartbeat_at: Optional[datetime] = None
    registered_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RuntimeJob(SQLModel, table=True):
    id: str = Field(primary_key=True)
    owner_subject: str = Field(index=True)
    mission_id: str = Field(default="", index=True)
    task_id: Optional[int] = Field(default=None, index=True)
    runtime_session_id: str = Field(default="", index=True)
    runtime_class: str = Field(default="container", index=True)
    image: str = Field(default="")
    command: str = Field(default="", sa_column=Column(Text))
    args_json: str = Field(default="[]", sa_column=Column(Text))
    env_json: str = Field(default="{}", sa_column=Column(Text))
    cwd: str = Field(default="")
    mounts_json: str = Field(default="[]", sa_column=Column(Text))
    artifact_rules_json: str = Field(default="{}", sa_column=Column(Text))
    timeout_seconds: int = Field(default=3600)
    restart_policy: str = Field(default="never")
    required_capabilities_json: str = Field(default="[]", sa_column=Column(Text))
    preferred_labels_json: str = Field(default="{}", sa_column=Column(Text))
    status: str = Field(default="queued", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class JobLease(SQLModel, table=True):
    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    node_id: str = Field(index=True)
    status: str = Field(default="leased", index=True)
    claimed_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    heartbeat_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_message: str = Field(default="", sa_column=Column(Text))
    cleanup_status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ExecutionSession(SQLModel, table=True):
    id: str = Field(primary_key=True)
    lease_id: str = Field(index=True)
    runtime_class: str = Field(default="container", index=True)
    pty_requested: bool = False
    attach_token_prefix: str = Field(default="")
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RuntimeNodeSpec(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    node_id: str = Field(index=True)
    config_json: str = Field(default="{}", sa_column=Column(Text))
    desired_version: str = Field(default="")
    upgrade_channel: str = Field(default="stable")
    drain_state: str = Field(default="active")
    health_summary: str = Field(default="")
    config_hash: str = Field(default="")
    last_reconcile_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RuntimeJoinToken(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    token_hash: str
    config_json: str = Field(default="{}", sa_column=Column(Text))
    upgrade_channel: str = Field(default="stable")
    desired_version: str = Field(default="")
    expires_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    status: str = Field(default="active")
    rotation_count: int = 0
    node_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class NodeEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: Optional[str] = Field(default=None, index=True)
    lease_id: Optional[str] = Field(default=None, index=True)
    event_type: str = Field(index=True)
    payload_json: str = Field(default="{}", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# mc-mesh work model
# ---------------------------------------------------------------------------


class MeshTask(SQLModel, table=True):
    """Agent-executable work unit inside a kluster's DAG.

    Distinct from the human-authored ``Task`` (task board).  ``MeshTask``
    records are dispatched to and claimed by agent runtimes supervised by
    mc-mesh.
    """

    id: Optional[str] = Field(default=None, primary_key=True)
    kluster_id: str = Field(index=True)
    mission_id: str = Field(index=True)  # denormalised for fast scoping
    parent_task_id: Optional[str] = Field(default=None, index=True)
    title: str
    description: str = Field(default="", sa_column=Column(Text))
    input_json: str = Field(default="{}", sa_column=Column(Text))
    # assigned | first_claim | broadcast
    claim_policy: str = Field(default="first_claim")
    # JSON array of MeshTask ids within the same kluster
    depends_on: str = Field(default="[]", sa_column=Column(Text))
    # JSON {"<artifact_name>": "<description>"}
    produces: str = Field(default="{}", sa_column=Column(Text))
    # JSON {"<artifact_name>": "<description>"}
    consumes: str = Field(default="{}", sa_column=Column(Text))
    # JSON ["claude_code"] or ["code.edit", "test.run"]
    required_capabilities: str = Field(default="[]", sa_column=Column(Text))
    # pending | ready | claimed | running | blocked | waiting_input |
    # finished | failed | cancelled
    status: str = Field(default="pending", index=True)
    claimed_by_agent_id: Optional[str] = Field(default=None, index=True)
    result_artifact_id: Optional[str] = Field(default=None)
    priority: int = Field(default=0, index=True)
    lease_expires_at: Optional[datetime] = None
    # Optimistic locking / claim hardening (migration aaa0420001)
    claim_lease_id: Optional[str] = Field(default=None)
    version_counter: int = Field(default=0)
    created_by_subject: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MeshAgent(SQLModel, table=True):
    """Agent runtime enrolled in a mission's durable pool.

    Agents enroll at the mission level and persist across klusters / tasks.

    Three JSON blobs capture the full agent picture:
    - profile_json: user-defined role, instructions, scope, constraints, permissions
    - machine_json: auto-detected by mc-mesh at enrollment (host, OS, CPU, RAM, tools)
    - runtime_json: runtime metadata reported by mc-mesh (model, context_window, etc.)
    """

    id: Optional[str] = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    # Optional: pin agent to a specific RuntimeNode (integrations/mc runtime)
    node_id: Optional[str] = Field(default=None, index=True)
    # claude_code | codex | gemini | custom
    runtime_kind: str = Field(index=True)
    runtime_version: str = ""
    # JSON list of capability strings
    capabilities: str = Field(default="[]", sa_column=Column(Text))
    labels: str = Field(default="{}", sa_column=Column(Text))
    # online | busy | idle | offline | errored
    status: str = Field(default="offline", index=True)
    current_task_id: Optional[str] = Field(default=None, index=True)
    enrolled_by_subject: str = ""
    enrolled_at: datetime = Field(default_factory=datetime.utcnow)
    last_heartbeat_at: Optional[datetime] = None
    runtime_node_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, ForeignKey("runtimenode.id"), nullable=True, index=True),
    )

    # --- Agent profile (user-defined) ---
    # Keys: name, role, description, instructions, scope, permissions, constraints
    profile_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # --- Machine info (auto-detected by mc-mesh daemon at enrollment) ---
    # Keys: hostname, os, cpu_cores, ram_gb, disk_free_gb, working_dir, installed_tools
    machine_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # --- Runtime metadata (reported by mc-mesh) ---
    # Keys: model, context_window, available_tools
    runtime_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # interactive | headless | solo
    supervision_mode: Optional[str] = Field(default=None)


class MeshProgressEvent(SQLModel, table=True):
    """Typed progress event emitted by an agent while executing a MeshTask.

    This is the primary new surface — structured, replayable, streamable.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    # Monotonically increasing per task (not globally unique)
    seq: int = Field(default=0)
    # phase_started | phase_finished | step_started | step_finished |
    # artifact_produced | artifact_consumed | waiting_on | unblocked |
    # needs_input | input_received | message_sent | message_received |
    # error | warning | info
    event_type: str = Field(index=True)
    phase: Optional[str] = None   # e.g. "planning", "editing", "testing"
    step: Optional[str] = None    # short human label
    summary: str = Field(default="", sa_column=Column(Text))
    # event-specific typed body (artifact ref, blocker id, input prompt…)
    payload_json: str = Field(default="{}", sa_column=Column(Text))
    occurred_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    # FK to AgentRun when this event was emitted inside a tracked run (migration aaa0423004)
    agent_run_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, ForeignKey("agentrun.id", ondelete="SET NULL")),
    )


class MeshMessage(SQLModel, table=True):
    """Mission- or kluster-scoped typed message between agents."""

    id: Optional[int] = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    kluster_id: Optional[str] = Field(default=None, index=True)
    from_agent_id: str = Field(index=True)
    # null = broadcast to scope
    to_agent_id: Optional[str] = Field(default=None, index=True)
    # null = not scoped to a specific task
    task_id: Optional[str] = Field(default=None, index=True)
    # coordination | handoff | question | answer | artifact_share | custom
    channel: str = Field(default="coordination")
    body_json: str = Field(default="{}", sa_column=Column(Text))
    in_reply_to: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    read_at: Optional[datetime] = None


class MeshTaskArtifact(SQLModel, table=True):
    """Link between a MeshTask and an entry in the Artifact ledger."""

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    artifact_id: int = Field(index=True)
    artifact_name: str = ""  # logical name declared in task produces/consumes
    role: str = "output"      # output | input
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentRun(SQLModel, table=True):
    """A single execution session for an agent, optionally tied to a MeshTask.

    Tracks the full lifecycle of an agent invocation: cost, status, checkpoints,
    and hierarchical nesting via parent_run_id.
    """

    __tablename__ = "agentrun"

    id: Optional[str] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    mesh_agent_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, ForeignKey("meshagent.id", ondelete="SET NULL"), index=True),
    )
    mesh_task_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, ForeignKey("meshtask.id", ondelete="SET NULL"), index=True),
    )
    # claude_code | codex | gemini | custom | shell | …
    runtime_kind: str
    runtime_session_id: Optional[str] = Field(default=None)
    # starting | running | paused | waiting_review | waiting_budget |
    # completed | failed | cancelled
    status: str = Field(default="starting", index=True)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    # Random UUID minted on creation; used by the agent to resume after restart
    resume_token: str
    last_checkpoint_at: Optional[datetime] = None
    total_cost_cents: int = Field(default=0)
    parent_run_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, ForeignKey("agentrun.id", ondelete="SET NULL")),
    )
    # Arbitrary JSON: tool call counts, model, custom agent metadata …
    metadata_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    idempotency_key: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BudgetPolicy(SQLModel, table=True):
    __tablename__ = "budgetpolicy"

    id: Optional[str] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    scope_type: str  # tenant|mission|kluster|agent|runtime|provider
    scope_id: str
    window_type: str  # day|week|month|rolling_24h
    hard_cap_cents: int
    soft_cap_cents: Optional[int] = Field(default=None)
    action_on_breach: str = Field(default="alert_only")  # pause|require_approval|alert_only
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BudgetWindow(SQLModel, table=True):
    __tablename__ = "budgetwindow"

    id: Optional[str] = Field(default=None, primary_key=True)
    policy_id: str = Field(
        sa_column=Column(String, ForeignKey("budgetpolicy.id", ondelete="CASCADE"), index=True)
    )
    window_start: datetime
    window_end: datetime
    consumed_cents: int = Field(default=0)
    state: str = Field(default="open")  # open|soft_tripped|hard_tripped|closed
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UsageRecord(SQLModel, table=True):
    __tablename__ = "usagerecord"

    id: Optional[str] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    run_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, ForeignKey("agentrun.id", ondelete="SET NULL")),
    )
    mesh_task_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, ForeignKey("meshtask.id", ondelete="SET NULL")),
    )
    mesh_agent_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, ForeignKey("meshagent.id", ondelete="SET NULL")),
    )
    mission_id: Optional[str] = Field(default=None)
    kluster_id: Optional[str] = Field(default=None)
    runtime_kind: str
    provider: str = Field(default="unknown")
    model: str = Field(default="unknown")
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    reasoning_tokens: int = Field(default=0)
    tool_calls: int = Field(default=0)
    wall_ms: int = Field(default=0)
    cost_cents: int = Field(default=0)
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
    source: str = Field(default="adapter")  # adapter|proxy|reconciled


class CostProfile(SQLModel, table=True):
    __tablename__ = "costprofile"
    __table_args__ = (
        UniqueConstraint("runtime_kind", "provider", "model", name="uq_costprofile_runtime_provider_model"),
    )

    id: Optional[str] = Field(default=None, primary_key=True)
    runtime_kind: str
    provider: str
    model: str
    input_rate_per_mtok_cents: int = Field(default=0)
    output_rate_per_mtok_cents: int = Field(default=0)
    reasoning_rate_per_mtok_cents: int = Field(default=0)
    tool_call_flat_cents: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RunCheckpoint(SQLModel, table=True):
    """Immutable checkpoint record within an AgentRun.

    Enables replay, audit, and cost attribution for individual tool calls,
    LLM turns, review gates, and manual pauses.
    """

    __tablename__ = "runcheckpoint"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_runcheckpoint_run_seq"),
    )

    id: Optional[str] = Field(default=None, primary_key=True)
    run_id: str = Field(
        sa_column=Column(String, ForeignKey("agentrun.id", ondelete="CASCADE"), index=True)
    )
    # Monotonically increasing within a run
    seq: int
    # tool_call | turn | review | publish | manual
    kind: str
    payload_json: str = Field(sa_column=Column(Text))


class ReviewGate(SQLModel, table=True):
    """Approval checkpoint that can block a MeshTask from completing."""

    __tablename__ = "reviewgate"

    id: Optional[str] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    mesh_task_id: str = Field(
        sa_column=Column(String, ForeignKey("meshtask.id"), index=True, nullable=False)
    )
    run_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String, ForeignKey("agentrun.id"), index=True, nullable=True),
    )
    # pre_tool | pre_publish | post_task | custom
    gate_type: str
    # auto | peer_agent | human | policy
    required_approvals: str = Field(default="human")
    # pending | approved | rejected | expired
    status: str = Field(default="pending")
    approval_request_id: Optional[str] = Field(default=None, nullable=True)
    ai_pending_action_id: Optional[str] = Field(default=None, nullable=True)
    policy_rule_id: Optional[str] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    resolved_at: Optional[datetime] = None


class MissionPack(SQLModel, table=True):
    __tablename__ = "missionpack"
    __table_args__ = (
        UniqueConstraint("owner_subject", "name", "version", name="uq_missionpack_owner_name_version"),
    )
    id: Optional[str] = Field(default=None, primary_key=True)
    owner_subject: str = Field(index=True)
    name: str
    version: int = Field(default=1)
    sha256: str
    signature: Optional[str] = None
    tarball_b64: str = Field(default="", sa_column=Column("tarball_b64", Text))
    manifest_json: str = Field(default="{}", sa_column=Column("manifest_json", Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
