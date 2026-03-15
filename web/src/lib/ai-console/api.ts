/**
 * Runtime-specific API calls for the AI Console.
 * Does NOT replace lib/api.ts — augments it with runtime-aware functions.
 */

import type { AiSession } from '$lib/api';
import type { CapabilitySet, RuntimeKind, RuntimePolicy } from './types';

const API_BASE = '';

function authHeader(token?: string) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Fetch capability sets for all registered runtime adapters. */
export async function listRuntimeCapabilities(token?: string): Promise<CapabilitySet[]> {
  const res = await fetch(`${API_BASE}/ai/runtime-capabilities`, {
    headers: authHeader(token)
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** Create an AI session with explicit runtime_kind and policy. */
export async function createAiSessionWithRuntime(
  token: string | undefined,
  opts: {
    title?: string;
    runtime_kind?: RuntimeKind;
    policy?: Partial<RuntimePolicy>;
  }
): Promise<AiSession> {
  const res = await fetch(`${API_BASE}/ai/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader(token) },
    body: JSON.stringify({
      title: opts.title ?? '',
      runtime_kind: opts.runtime_kind ?? 'opencode',
      policy: opts.policy ?? {}
    })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<AiSession>;
}
