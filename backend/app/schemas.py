from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class KlusterCreate(BaseModel):
    mission_id: Optional[str] = None
    name: str
    description: str = ""
    owners: str = ""
    contributors: str = ""
    tags: str = ""
    status: str = "active"


class KlusterRead(KlusterCreate):
    id: str
    created_at: datetime
    updated_at: datetime


class MissionCreate(BaseModel):
    name: str
    description: str = ""
    owners: str = ""
    contributors: str = ""
    tags: str = ""
    visibility: str = "public"
    status: str = "active"


class MissionUpdate(BaseModel):
    description: Optional[str] = None
    owners: Optional[str] = None
    contributors: Optional[str] = None
    tags: Optional[str] = None
    visibility: Optional[str] = None
    status: Optional[str] = None


class MissionOwnerTransfer(BaseModel):
    new_owner: str


class MissionRoleUpsert(BaseModel):
    subject: str
    role: str


class MissionRoleRead(BaseModel):
    id: int
    mission_id: str
    subject: str
    role: str
    created_at: datetime
    updated_at: datetime


class MissionRead(MissionCreate):
    id: str
    created_at: datetime
    updated_at: datetime


class DocCreate(BaseModel):
    kluster_id: str
    title: str
    body: str
    doc_type: str = "narrative"
    status: str = "draft"
    provenance: str = ""


class DocUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    doc_type: Optional[str] = None
    status: Optional[str] = None
    provenance: Optional[str] = None


class DocRead(DocCreate):
    id: int
    version: int
    created_at: datetime
    updated_at: datetime


class ArtifactCreate(BaseModel):
    kluster_id: str
    name: str
    artifact_type: str = "file"
    uri: str
    storage_backend: str = "inline"
    content_sha256: str = ""
    size_bytes: int = 0
    mime_type: str = ""
    status: str = "draft"
    provenance: str = ""


class ArtifactUpdate(BaseModel):
    name: Optional[str] = None
    artifact_type: Optional[str] = None
    uri: Optional[str] = None
    storage_backend: Optional[str] = None
    content_sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    status: Optional[str] = None
    provenance: Optional[str] = None


class ArtifactRead(ArtifactCreate):
    id: int
    version: int
    created_at: datetime
    updated_at: datetime


class EpicCreate(BaseModel):
    kluster_id: str
    title: str
    description: str = ""
    owner: str = ""
    status: str = "proposed"


class EpicRead(EpicCreate):
    id: int
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    kluster_id: str
    epic_id: Optional[int] = None
    title: str
    description: str = ""
    status: str = "proposed"
    owner: str = ""
    contributors: str = ""
    dependencies: str = ""
    definition_of_done: str = ""
    related_artifacts: str = ""


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None
    contributors: Optional[str] = None
    dependencies: Optional[str] = None
    definition_of_done: Optional[str] = None
    related_artifacts: Optional[str] = None


class TaskRead(TaskCreate):
    id: int
    public_id: str = ""
    created_at: datetime
    updated_at: datetime


class ExplorerTaskSummary(BaseModel):
    id: int
    kluster_id: str
    title: str
    status: str
    owner: str
    updated_at: datetime


class ExplorerKlusterSummary(BaseModel):
    id: str
    mission_id: Optional[str] = None
    name: str
    description: str
    status: str
    owners: str
    tags: str
    updated_at: datetime
    task_count: int
    task_status_counts: dict[str, int]
    recent_tasks: List[ExplorerTaskSummary]


class ExplorerMissionSummary(BaseModel):
    id: str
    name: str
    description: str
    status: str
    visibility: str
    owners: str
    tags: str
    updated_at: datetime
    kluster_count: int
    task_count: int
    klusters: List[ExplorerKlusterSummary]


class ExplorerTreeRead(BaseModel):
    generated_at: datetime
    mission_count: int
    kluster_count: int
    task_count: int
    missions: List[ExplorerMissionSummary]
    unassigned_klusters: List[ExplorerKlusterSummary]


class ExplorerNodeDetailRead(BaseModel):
    node_type: str
    node_id: str
    mission: Optional[MissionRead] = None
    kluster: Optional[KlusterRead] = None
    task: Optional[TaskRead] = None
    klusters: List[KlusterRead] = Field(default_factory=list)
    tasks: List[TaskRead] = Field(default_factory=list)


class OverlapSuggestionRead(BaseModel):
    id: int
    task_id: int
    candidate_task_id: int
    similarity_score: float
    evidence: str
    suggested_action: str
    created_at: datetime


class IngestionRequest(BaseModel):
    kluster_id: str
    config: dict = {}


class IngestionJobRead(BaseModel):
    id: int
    kluster_id: str
    source: str
    status: str
    config: str
    logs: str
    result_summary: str
    created_at: datetime
    updated_at: datetime


class MCPCall(BaseModel):
    tool: str
    args: dict = {}


class MCPTool(BaseModel):
    name: str
    description: str
    input_schema: dict


class MCPResponse(BaseModel):
    ok: bool
    result: dict = {}
    error: Optional[str] = None


class AgentCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    capabilities: str = ""
    status: str = "offline"
    agent_metadata: str = Field(default="", validation_alias="metadata", serialization_alias="metadata")


class AgentUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    capabilities: Optional[str] = None
    status: Optional[str] = None
    agent_metadata: Optional[str] = Field(
        default=None,
        validation_alias="metadata",
        serialization_alias="metadata",
    )


class AgentRead(AgentCreate):
    id: int
    created_at: datetime
    updated_at: datetime


class SkillBundleCreate(BaseModel):
    manifest: dict = Field(default_factory=dict)
    tarball_b64: str
    status: str = "active"
    signature_alg: str = ""
    signing_key_id: str = ""
    signature: str = ""


class SkillBundleRead(BaseModel):
    id: str
    scope_type: str
    scope_id: str
    mission_id: str
    kluster_id: str
    version: int
    status: str
    signature_alg: str
    signing_key_id: str
    signature: str
    signature_verified: bool
    manifest: dict
    sha256: str
    size_bytes: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class SkillSnapshotResolveRead(BaseModel):
    snapshot_id: str
    mission_id: str
    kluster_id: str
    effective_version: str
    sha256: str
    size_bytes: int
    mission_bundle_id: str
    kluster_bundle_id: str
    manifest: dict


class SkillSnapshotDownloadRead(BaseModel):
    snapshot_id: str
    sha256: str
    tarball_b64: str
    size_bytes: int
    manifest: dict


class SkillSyncStatusRead(BaseModel):
    mission_id: str
    kluster_id: str
    actor_subject: str
    agent_id: str
    last_snapshot_id: str
    last_snapshot_sha256: str
    local_overlay_sha256: str
    degraded_offline: bool
    drift_flag: bool
    drift_details: dict = Field(default_factory=dict)
    last_sync_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SkillSyncAck(BaseModel):
    mission_id: str
    kluster_id: str = ""
    agent_id: str = ""
    snapshot_id: str = ""
    snapshot_sha256: str = ""
    local_overlay_sha256: str = ""
    degraded_offline: bool = False
    drift_flag: bool = False
    drift_details: dict = Field(default_factory=dict)
    updated_at: datetime


class AgentSessionCreate(BaseModel):
    agent_id: int
    context: str = ""


class AgentSessionRead(BaseModel):
    id: int
    agent_id: int
    context: str
    started_at: datetime
    ended_at: Optional[datetime] = None


class TaskAssignmentCreate(BaseModel):
    task_id: int
    agent_id: int
    status: str = "available"


class TaskAssignmentUpdate(BaseModel):
    status: Optional[str] = None


class TaskAssignmentRead(TaskAssignmentCreate):
    id: int
    created_at: datetime
    updated_at: datetime


class AgentMessageSend(BaseModel):
    to_agent_id: int
    content: str = ""
    message_type: str = "info"
    task_id: Optional[int] = None


class AgentMessageRead(BaseModel):
    id: int
    from_agent_id: int
    to_agent_id: int
    content: str
    message_type: str
    task_id: Optional[int] = None
    read: bool
    created_at: datetime


class GovernancePolicyRead(BaseModel):
    id: int
    version: int
    state: str
    policy: dict
    change_note: str
    created_by: str
    published_by: str
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class GovernancePolicyDraftCreate(BaseModel):
    policy: Optional[dict] = None
    change_note: str = ""


class GovernancePolicyDraftUpdate(BaseModel):
    policy: dict
    change_note: str = ""


class GovernancePolicyPublish(BaseModel):
    change_note: str = ""


class GovernancePolicyRollback(BaseModel):
    version: int
    change_note: str = ""


class GovernancePolicyEventRead(BaseModel):
    id: int
    policy_id: Optional[int] = None
    version: int
    event_type: str
    actor_subject: str
    detail: dict
    created_at: datetime


class ApprovalRequestCreate(BaseModel):
    mission_id: str
    action: str
    reason: str = ""
    channel: str = "api"
    target_entity_type: str = ""
    target_entity_id: str = ""
    request_context: dict = {}
    expires_in_seconds: int = 900


class ApprovalRequestDecision(BaseModel):
    note: str = ""
    expires_in_seconds: int = 900


class ApprovalRequestRead(BaseModel):
    id: int
    mission_id: str
    action: str
    channel: str
    reason: str
    target_entity_type: str
    target_entity_id: str
    request_context: dict
    status: str
    requested_by: str
    approved_by: str
    rejected_by: str
    decision_note: str
    approval_expires_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    executed_action: str
    executed_request_id: str
    created_at: datetime
    updated_at: datetime


class ApprovalDecisionRead(BaseModel):
    approval: ApprovalRequestRead
    approval_token: str


class SlackChannelBindingCreate(BaseModel):
    provider: str = "slack"
    mission_id: str
    workspace_external_id: str = ""
    channel_id: str
    channel_name: str = ""
    channel_metadata: dict = Field(default_factory=dict)


class SlackChannelBindingRead(BaseModel):
    id: int
    provider: str
    mission_id: str
    workspace_external_id: str
    channel_id: str
    channel_name: str
    channel_metadata: dict
    created_by: str
    created_at: datetime
    updated_at: datetime


class FeedbackCreate(BaseModel):
    mission_id: str
    kluster_id: str = ""
    provider: str = ""
    channel_id: str = ""
    category: str = ""
    severity: str = "medium"
    summary: str
    recommendation: str = ""
    metadata: dict = Field(default_factory=dict)


class FeedbackRead(BaseModel):
    id: int
    mission_id: str
    kluster_id: str
    source_type: str
    source_subject: str
    provider: str
    channel_id: str
    category: str
    severity: str
    summary: str
    recommendation: str
    status: str
    triage_status: str
    priority: str
    owner: str
    disposition: str
    outcome_ref: str
    metadata: dict
    created_at: datetime
    updated_at: datetime


class FeedbackSummaryRead(BaseModel):
    mission_id: str
    total: int
    by_source_type: dict[str, int]
    by_severity: dict[str, int]
    by_category: dict[str, int]
    by_triage_status: dict[str, int]
    by_priority: dict[str, int]


class FeedbackTriageUpdate(BaseModel):
    triage_status: Optional[str] = None
    priority: Optional[str] = None
    owner: Optional[str] = None
    disposition: Optional[str] = None
    outcome_ref: Optional[str] = None


class UserProfileCreate(BaseModel):
    name: str
    description: str = ""
    is_default: bool = False
    manifest: List[dict] = Field(default_factory=list)
    tarball_b64: str
    expected_sha256: Optional[str] = None


class UserProfileUpdate(BaseModel):
    description: Optional[str] = None
    is_default: Optional[bool] = None
    manifest: Optional[List[dict]] = None
    tarball_b64: Optional[str] = None
    expected_sha256: Optional[str] = None


class UserProfileRead(BaseModel):
    id: int
    name: str
    owner_subject: str
    description: str
    is_default: bool
    manifest: List[dict]
    sha256: Optional[str]
    size_bytes: int
    created_at: datetime
    updated_at: datetime


class UserProfileDownloadRead(UserProfileRead):
    tarball_b64: str


class AiSessionCreate(BaseModel):
    title: str = ""
    runtime_kind: str = "opencode"
    policy: dict = Field(default_factory=dict)


class AiTurnCreate(BaseModel):
    message: str


class AiTurnRead(BaseModel):
    id: int
    role: str
    content: dict
    created_at: datetime


class AiEventRead(BaseModel):
    id: int
    turn_id: Optional[int] = None
    event_type: str
    payload: dict
    created_at: datetime


class AiPendingActionRead(BaseModel):
    id: str
    tool: str
    args: dict
    reason: str
    status: str
    requested_by: str
    approved_by: str
    rejected_by: str
    rejection_note: str
    created_at: datetime
    updated_at: datetime


class AiSessionRead(BaseModel):
    id: str
    owner_subject: str
    title: str
    status: str
    runtime_kind: str = "opencode"
    runtime_session_id: Optional[str] = None
    workspace_path: Optional[str] = None
    capability_snapshot: dict = Field(default_factory=dict)
    policy: dict = Field(default_factory=dict)
    turns: List[AiTurnRead] = Field(default_factory=list)
    events: List[AiEventRead] = Field(default_factory=list)
    pending_actions: List[AiPendingActionRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
