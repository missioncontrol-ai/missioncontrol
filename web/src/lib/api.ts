const API_BASE = '';

function authHeader(token?: string) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function fetchTree(token?: string) {
  const res = await fetch(`${API_BASE}/explorer/tree`, {
    headers: authHeader(token)
  });
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

export async function fetchNode(type: string, id: string, token?: string) {
  const res = await fetch(`${API_BASE}/explorer/node/${type}/${id}`, {
    headers: authHeader(token)
  });
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

export async function fetchPolicy(token?: string) {
  const res = await fetch(`${API_BASE}/governance/policy/active`, {
    headers: authHeader(token)
  });
  return res.ok ? res.json() : Promise.reject(new Error(res.statusText));
}

export async function fetchGovernanceEvents(token?: string) {
  const res = await fetch(`${API_BASE}/governance/policy/events?limit=10`, {
    headers: authHeader(token)
  });
  return res.ok ? res.json() : [];
}

export type ExplorerTree = {
  missions?: any[];
  klusters?: any[];
  tasks?: any[];
};

export type PolicySummary = {
  version?: string;
  name?: string;
  description?: string;
};

export type AiTurn = {
  id: number;
  role: 'user' | 'assistant' | 'tool';
  content: Record<string, any>;
  created_at: string;
};

export type AiEvent = {
  id: number;
  turn_id?: number | null;
  event_type: string;
  payload: Record<string, any>;
  created_at: string;
};

export type AiPendingAction = {
  id: string;
  tool: string;
  args: Record<string, any>;
  reason: string;
  status: string;
  requested_by: string;
  approved_by: string;
  rejected_by: string;
  rejection_note: string;
  created_at: string;
  updated_at: string;
};

export type AiSession = {
  id: string;
  owner_subject: string;
  title: string;
  status: string;
  // Runtime layer fields (added in Phase 2)
  runtime_kind?: string;
  runtime_session_id?: string | null;
  workspace_path?: string | null;
  capability_snapshot?: Record<string, unknown>;
  policy?: Record<string, unknown>;
  turns: AiTurn[];
  events: AiEvent[];
  pending_actions: AiPendingAction[];
  created_at: string;
  updated_at: string;
};

export async function createAiSession(token?: string, title = '', runtimeKind?: string, policy?: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/ai/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader(token) },
    body: JSON.stringify({ title, runtime_kind: runtimeKind ?? 'opencode', policy: policy ?? {} })
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as AiSession;
}

export async function listAiSessions(token?: string) {
  const res = await fetch(`${API_BASE}/ai/sessions?limit=20`, {
    headers: authHeader(token)
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as AiSession[];
}

export async function getAiSession(sessionId: string, token?: string, sinceEventId = 0) {
  const res = await fetch(`${API_BASE}/ai/sessions/${encodeURIComponent(sessionId)}?since_event_id=${sinceEventId}`, {
    headers: authHeader(token)
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as AiSession;
}

export async function sendAiTurn(sessionId: string, message: string, token?: string) {
  const res = await fetch(`${API_BASE}/ai/sessions/${encodeURIComponent(sessionId)}/turns`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader(token) },
    body: JSON.stringify({ message })
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as AiSession;
}

export async function approveAiAction(sessionId: string, actionId: string, token?: string) {
  const res = await fetch(
    `${API_BASE}/ai/sessions/${encodeURIComponent(sessionId)}/actions/${encodeURIComponent(actionId)}/approve`,
    {
      method: 'POST',
      headers: authHeader(token)
    }
  );
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as AiSession;
}

export async function rejectAiAction(sessionId: string, actionId: string, token?: string, note = '') {
  const res = await fetch(
    `${API_BASE}/ai/sessions/${encodeURIComponent(sessionId)}/actions/${encodeURIComponent(actionId)}/reject?note=${encodeURIComponent(note)}`,
    {
      method: 'POST',
      headers: authHeader(token)
    }
  );
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as AiSession;
}

// ── Evolve ────────────────────────────────────────────────────────────────────

export type EvolveRun = {
  run_id: string;
  agent: string;
  started_at: string;
  status: string;
  ai_session_id?: string | null;
};

export type EvolveMissionStatus = {
  mission_id: string;
  status: string;
  created_at: string;
  task_count: number;
  run_count: number;
  runs: EvolveRun[];
};

export async function seedEvolveMission(spec: Record<string, unknown>, token?: string) {
  const res = await fetch(`${API_BASE}/evolve/missions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader(token) },
    body: JSON.stringify({ spec })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function runEvolveMission(
  missionId: string,
  runtimeKind = 'opencode',
  policy: Record<string, unknown> = {},
  token?: string
) {
  const res = await fetch(`${API_BASE}/evolve/missions/${encodeURIComponent(missionId)}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader(token) },
    body: JSON.stringify({ runtime_kind: runtimeKind, policy })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getEvolveMissionStatus(missionId: string, token?: string) {
  const res = await fetch(
    `${API_BASE}/evolve/missions/${encodeURIComponent(missionId)}/status`,
    { headers: authHeader(token) }
  );
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as EvolveMissionStatus;
}

// ── Scheduled Jobs ────────────────────────────────────────────────────────────

export type ScheduledJob = {
  id: number;
  owner_subject: string;
  name: string;
  description: string;
  cron_expr: string;
  runtime_kind: string;
  initial_prompt: string;
  system_context?: string | null;
  policy: Record<string, unknown>;
  enabled: boolean;
  last_run_at?: string | null;
  last_session_id?: string | null;
  created_at: string;
  updated_at: string;
};

export async function listScheduledJobs(token?: string) {
  const res = await fetch(`${API_BASE}/scheduled-jobs`, { headers: authHeader(token) });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ScheduledJob[];
}

export async function createScheduledJob(
  data: {
    name: string;
    cron_expr: string;
    initial_prompt: string;
    description?: string;
    runtime_kind?: string;
    system_context?: string;
    policy?: Record<string, unknown>;
    enabled?: boolean;
  },
  token?: string
) {
  const res = await fetch(`${API_BASE}/scheduled-jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader(token) },
    body: JSON.stringify(data)
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ScheduledJob;
}

export async function getScheduledJob(jobId: number, token?: string) {
  const res = await fetch(`${API_BASE}/scheduled-jobs/${jobId}`, { headers: authHeader(token) });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ScheduledJob;
}

export async function updateScheduledJob(
  jobId: number,
  data: Partial<{
    name: string;
    description: string;
    cron_expr: string;
    runtime_kind: string;
    initial_prompt: string;
    system_context: string;
    policy: Record<string, unknown>;
    enabled: boolean;
  }>,
  token?: string
) {
  const res = await fetch(`${API_BASE}/scheduled-jobs/${jobId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeader(token) },
    body: JSON.stringify(data)
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ScheduledJob;
}

export async function deleteScheduledJob(jobId: number, token?: string) {
  const res = await fetch(`${API_BASE}/scheduled-jobs/${jobId}`, {
    method: 'DELETE',
    headers: authHeader(token)
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerScheduledJobNow(jobId: number, token?: string) {
  const res = await fetch(`${API_BASE}/scheduled-jobs/${jobId}/run`, {
    method: 'POST',
    headers: authHeader(token)
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── OIDC ──────────────────────────────────────────────────────────────────────

export type OidcExchangeResponse = {
  token: string;
  subject: string;
  expires_at: string;
  session_id: number;
  ttl_hours: number;
};

export async function exchangeOidcGrant(grantId: string) {
  const res = await fetch(`${API_BASE}/auth/oidc/exchange`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ grant_id: grantId })
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as OidcExchangeResponse;
}
