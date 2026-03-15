/**
 * TypeScript equivalents of the Python AI Console domain contracts.
 * Mirrors backend/app/ai_console/contracts.py.
 */

export type RuntimeKind = 'opencode' | 'claude_code' | 'codex' | 'native';

export type CapabilitySet = {
  runtime_kind: RuntimeKind;
  display_name: string;
  icon_slug: string;
  supports_streaming: boolean;
  supports_file_workspace: boolean;
  supports_tool_interception: boolean;
  supports_skill_packs: boolean;
  supports_session_resume: boolean;
  max_context_tokens: number;
};

export type NormalizedEventFamily =
  | 'lifecycle'
  | 'io'
  | 'tool'
  | 'approval'
  | 'view'
  | 'runtime';

export type NormalizedEvent = {
  schema_version: 1;
  family: NormalizedEventFamily;
  event_type: string;
  session_id: string;
  turn_id: number | null;
  runtime_kind: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type RuntimePolicy = {
  allowed_tools: string[];
  denied_tools: string[];
  max_turns_per_session: number;
  require_approval_for_writes: boolean;
  workspace_ttl_seconds: number;
};
