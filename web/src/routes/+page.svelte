<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { authStore, loginWithToken, logout, startOidcLogin, token } from '$lib/auth';
  import {
    matrixEvents,
    matrixStatus,
    startMatrixStream,
    stopMatrixStream
  } from '$lib/telemetry';
import {
  fetchTree,
  fetchPolicy,
  fetchGovernanceEvents
} from '$lib/api';
import type { ExplorerTree, PolicySummary } from '$lib/api';
  import { derived, get } from 'svelte/store';

  let initialToken = '';
  let selectedTab: 'matrix' | 'explorer' | 'onboarding' | 'governance' = 'matrix';
  let tree: ExplorerTree = {};
  let selectedNode: any = null;
  let policy: PolicySummary | null = null;
  let policyEvents: any[] = [];
  let onboardingEndpoint = 'https://mc-dev.merlinlabs.cloud';
  let onboardingManifest = '';
  let manifestUrl = '';
  let statusMessage = '';
  let searchInput = '';

  const hasEvents = derived(matrixEvents, ($events) => $events.length > 0);
  const lastEvent = derived(matrixEvents, ($events) => ($events.length ? $events[0] : null));

  function handleToken() {
    if (!initialToken.trim()) {
      statusMessage = 'Enter a MissionControl token or use OIDC login.';
      return;
    }
    loginWithToken(initialToken.trim());
  }

  function handleOidc() {
    startOidcLogin();
  }

  async function refreshTree() {
    try {
      const data = await fetchTree(get(token));
      tree = data;
      statusMessage = 'Explorer refreshed';
    } catch (err) {
      statusMessage = err instanceof Error ? err.message : 'Failed to fetch tree';
    }
  }

  async function refreshPolicy() {
    policy = null;
    const data = await fetchPolicy(get(token));
    policy = data;
  }

  async function refreshPolicyEvents() {
    policyEvents = await fetchGovernanceEvents(get(token));
  }

  async function loadManifest() {
    manifestUrl = `${onboardingEndpoint.replace(/\/$/, '')}/agent-onboarding.json`;
    onboardingManifest = JSON.stringify(
      {
        endpoint: onboardingEndpoint,
        token: get(token),
        generatedAt: new Date().toISOString()
      },
      null,
      2
    );
  }

  function selectTab(tab: typeof selectedTab) {
    selectedTab = tab;
  }

  onMount(() => {
    const unsubscribe = authStore.subscribe(($auth) => {
      if ($auth.loggedIn) {
        startMatrixStream($auth.token ?? undefined);
        refreshTree();
        refreshPolicy();
        refreshPolicyEvents();
      } else {
        stopMatrixStream();
      }
    });
    return () => {
      unsubscribe();
      stopMatrixStream();
    };
  });

  const eventChunks = derived(matrixEvents, ($events) =>
    $events.map((event) => ({
      label: event.type ?? 'matrix',
      mission: event.mission_id,
      status: event.status,
      detail: event.payload,
      time: new Date(event.receivedAt).toLocaleTimeString()
    }))
  );
</script>

<style>
  .main-shell {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    padding: 1rem 2rem 2rem;
  }
</style>

{#if $authStore.loggedIn}
  <div class="main-shell">
    <section class="tabs">
      <button class={`tab ${selectedTab === 'matrix' ? 'active' : ''}`} on:click={() => selectTab('matrix')}>Matrix</button>
      <button class={`tab ${selectedTab === 'explorer' ? 'active' : ''}`} on:click={() => selectTab('explorer')}>Explorer</button>
      <button class={`tab ${selectedTab === 'onboarding' ? 'active' : ''}`} on:click={() => selectTab('onboarding')}>Onboarding</button>
      <button class={`tab ${selectedTab === 'governance' ? 'active' : ''}`} on:click={() => selectTab('governance')}>Governance</button>
    </section>

    {#if selectedTab === 'matrix'}
      <div class="glass-panel">
        <div class="grid">
          <div>
            <div class="status-chip">
              Matrix stream { $matrixStatus.connected ? 'live' : 'offline' }
            </div>
            <p class="muted">Rate limit: { $matrixStatus.rateLimit?.remaining ?? '—' } / { $matrixStatus.rateLimit?.limit ?? '—' }</p>
          </div>
          <div class="status-chip">
            Last event: {#if $lastEvent}{$lastEvent.time}{:else}waiting...{/if}
          </div>
        </div>
        <div class="matrix-grid">
          {#each $matrixEvents.slice(0, 4) as event (event.receivedAt)}
            <article class="glass-panel event-pill">
              <strong>{event.type ?? 'matrix'}</strong>
              <p>{event.payload?.title ?? 'update'}</p>
              <div class="status-chip">
                {event.status ?? 'status unknown'}
                {#if event.rate_limit}
                  · resets {new Date(event.rate_limit.reset_at).toLocaleTimeString()}
                {/if}
              </div>
            </article>
          {:else}
            <p>No events yet.</p>
          {/each}
        </div>
        <div class="matrix-timeline">
          {#each $eventChunks as chunk}
            <div class="event-pill">
              <small>{chunk.time}</small>
              <p>{chunk.label} — {chunk.status ?? 'pending'} </p>
              <p class="muted">{chunk.detail?.summary ?? JSON.stringify(chunk.detail)}</p>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    {#if selectedTab === 'explorer'}
      <div class="glass-panel">
        <div class="grid">
          <div>
            <h3>Mission Tree</h3>
            <button class="ghost" on:click={refreshTree}>Refresh</button>
          </div>
          <div class="status-chip">Search: {searchInput || 'all'}</div>
        </div>
        <div class="grid">
          <section class="glass-panel">
            <h4>Missions</h4>
            <ul>
              {#each tree.missions ?? [] as mission}
                <li>
                  <button class="ghost" on:click={() => (selectedNode = mission)}>
                    {mission.name}
                  </button>
                </li>
              {:else}
                <li>No missions yet</li>
              {/each}
            </ul>
          </section>
          <section class="glass-panel">
            <h4>Details</h4>
            <pre>{selectedNode ? JSON.stringify(selectedNode, null, 2) : 'Choose a node'}</pre>
          </section>
        </div>
      </div>
    {/if}

    {#if selectedTab === 'onboarding'}
      <div class="glass-panel">
        <h3>Agent Onboarding</h3>
        <label>
          Endpoint
          <input bind:value={onboardingEndpoint} placeholder="https://mc.example.com" />
        </label>
        <div class="onboarding-actions">
          <button class="ghost" on:click={loadManifest}>Regenerate Manifest</button>
          <button class="ghost" on:click={() => navigator.clipboard.writeText(onboardingManifest || '')}>Copy</button>
        </div>
        <div class="grid">
          <section class="glass-panel">
            <h4>Manifest URL</h4>
            <code>{manifestUrl || 'fetch to generate'}</code>
          </section>
          <section class="glass-panel">
            <h4>Manifest Preview</h4>
            <pre>{onboardingManifest || 'No manifest yet.'}</pre>
          </section>
        </div>
      </div>
    {/if}

    {#if selectedTab === 'governance'}
      <div class="glass-panel">
        <div class="grid">
          <section class="glass-panel">
            <h4>Active Policy</h4>
            <pre>{policy ? JSON.stringify(policy, null, 2) : 'Loading...'}</pre>
          </section>
          <section class="glass-panel">
            <h4>Policy Events</h4>
            <ul>
              {#each policyEvents as evt}
                <li class="muted">
                  [{evt.level}] {evt.message}
                </li>
              {:else}
                <li>No events yet.</li>
              {/each}
            </ul>
          </section>
        </div>
        <div class="onboarding-actions">
          <button class="ghost" on:click={refreshPolicy}>Refresh Policy</button>
          <button class="ghost" on:click={refreshPolicyEvents}>Refresh Events</button>
        </div>
      </div>
    {/if}

    {#if statusMessage}
      <div class="glass-panel error">{statusMessage}</div>
    {/if}
  </div>
{:else}
  <section class="login">
    <div class="login-card">
      <div class="status-chip">MissionControl Secure</div>
      <h1>Team Console</h1>
      <label>
        Access Token
        <input bind:value={initialToken} type="password" placeholder="MC_TOKEN" />
      </label>
      <div class="login-actions">
        <button class="primary" on:click={handleToken}>Continue with token</button>
        <button class="ghost" on:click={handleOidc}>Sign in via OIDC</button>
      </div>
      <p class="muted">
        The new interface now supports OIDC login (token passthrough) plus legacy tokens for quick testing.
      </p>
    </div>
  </section>
{/if}
