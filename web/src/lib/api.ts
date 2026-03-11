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
