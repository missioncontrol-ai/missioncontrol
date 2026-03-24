<script lang="ts">
  import { onMount, onDestroy, tick } from 'svelte';
  import { authStore, bootstrapAuth, loginWithCookieSession, loginWithToken, token, startOidcLogin } from '$lib/auth';
  import {
    fetchTree,
    fetchPolicy,
    fetchGovernanceEvents,
    createAiSession,
    listAiSessions,
    getAiSession,
    sendAiTurn,
    approveAiAction,
    rejectAiAction,
    exchangeOidcGrant,
    type AiSession,
    type AiEvent,
    type AiTurn,
    type ExplorerTree,
    type PolicySummary
  } from '$lib/api';
  import { matrixEvents, matrixStatus, startMatrixStream, stopMatrixStream } from '$lib/telemetry';
  import { derived, get } from 'svelte/store';

  type UiEntry = {
    key: string;
    kind: 'user' | 'assistant' | 'event';
    title: string;
    body: string;
    eventType?: string;
    payload?: Record<string, any>;
    createdAt: string;
  };
  type TabName = 'ai' | 'matrix' | 'explorer' | 'onboarding' | 'governance';
  const TAB_STORAGE_KEY = 'mc.ui.selected_tab';
  const TAB_NAMES: TabName[] = ['ai', 'matrix', 'explorer', 'onboarding', 'governance'];

  let initialToken = '';
  let selectedTab: TabName = 'ai';
  let tree: ExplorerTree = {};
  let selectedNode: any = null;
  let selectedNodeType: 'mission' | 'kluster' | 'task' | null = null;
  let selectedNodeData: any = null;
  let explorerBusy = false;
  let policy: PolicySummary | null = null;
  let policyEvents: any[] = [];
  let onboardingEndpoint = '';
  let onboardingManifest = '';
  let manifestUrl = '';
  let statusMessage = '';
  let toastVisible = false;
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  let searchInput = '';
  let lastRefreshed = '';

  let aiSessions: AiSession[] = [];
  let activeSession: AiSession | null = null;
  let aiInput = '';
  let aiBusy = false;
  let aiError = '';
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let lastEventId = 0;
  let refreshInFlight = false;
  let terminalEl: HTMLDivElement | null = null;
  let pinToBottom = true;
  let showEventDebug = false;

  const lastEvent = derived(matrixEvents, ($events) => ($events.length ? $events[0] : null));
  const eventChunks = derived(matrixEvents, ($events) =>
    $events.map((event) => ({
      label: event.type ?? 'matrix',
      status: event.status,
      detail: event.payload,
      time: new Date(event.receivedAt).toLocaleTimeString()
    }))
  );

  function handleToken() {
    if (!initialToken.trim()) {
      showToast('Enter a MissionControl token or use OIDC login.');
      return;
    }
    loginWithToken(initialToken.trim());
  }

  function handleOidc() {
    startOidcLogin(window.location.pathname);
  }

  function isTabName(value: string | null): value is TabName {
    return value !== null && TAB_NAMES.includes(value as TabName);
  }

  function setSelectedTab(tab: TabName) {
    selectedTab = tab;
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(TAB_STORAGE_KEY, tab);
    }
  }

  function showToast(msg: string) {
    statusMessage = msg;
    toastVisible = true;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastVisible = false;
    }, 4000);
  }

  function summarizeEvent(event: AiEvent): { title: string; body: string } {
    const payload = event.payload ?? {};
    if (event.event_type === 'tool_call') return { title: 'Tool call', body: `${payload.tool ?? 'unknown'} ${JSON.stringify(payload.args ?? {})}` };
    if (event.event_type === 'tool_result') {
      const ok = Boolean(payload.result?.ok);
      if (ok) return { title: 'Tool result', body: `${payload.tool ?? 'tool'} completed` };
      return {
        title: 'Tool issue',
        body: `I could not complete ${payload.tool ?? 'that tool'} this time. Expand details for the technical error.`
      };
    }
    if (event.event_type === 'approval_required') return { title: 'Approval required', body: `${payload.tool ?? 'action'} is waiting for approval` };
    if (event.event_type === 'approval_outcome') return { title: 'Approval outcome', body: `${payload.action_id ?? 'action'} ${payload.status ?? ''}` };
    if (event.event_type === 'view_rendered') return { title: 'View prepared', body: `${payload.view?.title ?? 'Custom view'} ready` };
    if (event.event_type === 'planner_result') return { title: 'Planner result', body: payload.assistant_text ?? 'Plan generated' };
    if (event.event_type === 'session_started') return { title: 'Session started', body: payload.title ?? 'AI session' };
    return { title: event.event_type, body: 'Event captured' };
  }

  function buildTranscript(session: AiSession | null): UiEntry[] {
    if (!session) return [];
    const entries: UiEntry[] = [];
    for (const t of session.turns ?? []) {
      const text = String((t.content ?? {}).text ?? '').trim() || JSON.stringify(t.content ?? {});
      entries.push({
        key: `turn-${t.id}`,
        kind: t.role === 'assistant' ? 'assistant' : 'user',
        title: t.role === 'assistant' ? 'MissionControl' : 'You',
        body: text,
        payload: t.content,
        createdAt: t.created_at
      });
    }
    for (const e of session.events ?? []) {
      if (e.event_type === 'user_message') continue;
      if (!showEventDebug && (e.event_type === 'planner_result' || e.event_type === 'session_started')) continue;
      const s = summarizeEvent(e);
      entries.push({
        key: `event-${e.id}`,
        kind: 'event',
        title: s.title,
        body: s.body,
        eventType: e.event_type,
        payload: e.payload,
        createdAt: e.created_at
      });
    }
    return entries.sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
  }

  async function maybeScrollToBottom(force = false) {
    await tick();
    if (!terminalEl) return;
    if (force || pinToBottom) {
      terminalEl.scrollTop = terminalEl.scrollHeight;
    }
  }

  function onTranscriptScroll() {
    if (!terminalEl) return;
    const delta = terminalEl.scrollHeight - terminalEl.scrollTop - terminalEl.clientHeight;
    pinToBottom = delta < 48;
  }

  async function initAi() {
    try {
      aiSessions = await listAiSessions(get(token) || undefined);
      activeSession = aiSessions[0] ?? null;
      if (!activeSession) {
        activeSession = await createAiSession(get(token) || undefined, 'AI Console Session');
      }
      activeSession = await getAiSession(activeSession.id, get(token) || undefined, 0);
      lastEventId = activeSession.events.length ? Math.max(...activeSession.events.map((e) => e.id ?? 0)) : 0;
      aiError = '';
      await maybeScrollToBottom(true);
    } catch (err) {
      aiError = err instanceof Error ? err.message : 'Failed to initialize AI session';
    }
  }

  async function refreshActiveSession() {
    if (!activeSession || refreshInFlight) return;
    refreshInFlight = true;
    try {
      const session = await getAiSession(activeSession.id, get(token) || undefined, lastEventId);
      const mergedEvents = [...(activeSession.events ?? []), ...(session.events ?? [])]
        .filter((value, index, array) => array.findIndex((item) => item.id === value.id) === index)
        .sort((a, b) => (a.id ?? 0) - (b.id ?? 0));
      if (session.events.length > 0) {
        lastEventId = Math.max(lastEventId, ...session.events.map((e) => e.id ?? 0));
      }
      activeSession = {
        ...session,
        events: mergedEvents
      };
      aiError = '';
      await maybeScrollToBottom();
    } catch (err) {
      aiError = err instanceof Error ? err.message : 'Session refresh failed';
    } finally {
      refreshInFlight = false;
    }
  }

  async function newAiSession() {
    try {
      const session = await createAiSession(get(token) || undefined, 'AI Console Session');
      activeSession = session;
      aiSessions = [session, ...aiSessions];
      lastEventId = 0;
      pinToBottom = true;
      await maybeScrollToBottom(true);
    } catch (err) {
      aiError = err instanceof Error ? err.message : 'Failed to create AI session';
    }
  }

  async function sendAiMessage() {
    const message = aiInput.trim();
    if (!message || !activeSession || aiBusy) return;
    aiBusy = true;
    pinToBottom = true;
    try {
      activeSession = await sendAiTurn(activeSession.id, message, get(token) || undefined);
      aiInput = '';
      lastEventId = activeSession.events.length ? Math.max(...activeSession.events.map((e) => e.id ?? 0)) : 0;
      aiError = '';
      await maybeScrollToBottom(true);
    } catch (err) {
      aiError = err instanceof Error ? err.message : 'Send failed';
    } finally {
      aiBusy = false;
    }
  }

  async function approve(actionId: string) {
    if (!activeSession) return;
    aiBusy = true;
    try {
      activeSession = await approveAiAction(activeSession.id, actionId, get(token) || undefined);
      await maybeScrollToBottom(true);
    } catch (err) {
      aiError = err instanceof Error ? err.message : 'Approval failed';
    } finally {
      aiBusy = false;
    }
  }

  async function reject(actionId: string) {
    if (!activeSession) return;
    aiBusy = true;
    try {
      activeSession = await rejectAiAction(activeSession.id, actionId, get(token) || undefined);
      await maybeScrollToBottom(true);
    } catch (err) {
      aiError = err instanceof Error ? err.message : 'Reject failed';
    } finally {
      aiBusy = false;
    }
  }

  async function refreshTree() {
    try {
      const data = await fetchTree(get(token) || undefined);
      tree = data;
      lastRefreshed = new Date().toLocaleTimeString();
      if (!selectedNode && tree.missions?.length) {
        selectMission(tree.missions[0]);
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to fetch tree');
    }
  }

  async function selectExplorerNode(type: 'mission' | 'kluster' | 'task', node: any) {
    selectedNode = node;
    selectedNodeType = type;
    if (type === 'mission') {
      selectedNodeData = {
        mission: node,
        klusters: node.klusters ?? [],
        tasks: []
      };
      return;
    }
    const nodeId = type === 'task' ? String(node.public_id ?? node.id ?? '') : String(node.id ?? '');
    if (!nodeId) {
      selectedNodeData = null;
      return;
    }
    explorerBusy = true;
    try {
      selectedNodeData = await fetchNode(type, nodeId, get(token) || undefined);
    } catch (err) {
      selectedNodeData = null;
      showToast(err instanceof Error ? err.message : 'Failed to load explorer node');
    } finally {
      explorerBusy = false;
    }
  }

  function selectMission(mission: any) {
    return selectExplorerNode('mission', mission);
  }

  function selectKluster(kluster: any) {
    return selectExplorerNode('kluster', kluster);
  }

  function selectTask(task: any) {
    return selectExplorerNode('task', task);
  }

  function statusClass(status?: string) {
    const value = String(status ?? '').toLowerCase();
    if (value === 'done' || value === 'completed') return 'status-done';
    if (value === 'blocked') return 'status-blocked';
    if (value === 'in_progress') return 'status-progress';
    return 'status-proposed';
  }

  function taskCountByStatus(tasks: any[] = [], status: string) {
    return tasks.filter((t) => String(t.status ?? '').toLowerCase() === status).length;
  }

  async function refreshPolicy() {
    policy = await fetchPolicy(get(token) || undefined);
  }

  async function refreshPolicyEvents() {
    policyEvents = await fetchGovernanceEvents(get(token) || undefined);
  }

  function defaultOnboardingEndpoint() {
    if (typeof window === 'undefined') return 'https://mc.missioncontrolai.app';
    return window.location.origin;
  }

  async function syncOnboardingEndpoint() {
    const localManifestUrl = `${defaultOnboardingEndpoint().replace(/\/$/, '')}/agent-onboarding.json`;
    try {
      const res = await fetch(localManifestUrl);
      if (!res.ok) return;
      const manifest = await res.json();
      onboardingEndpoint = String(manifest?.generated_for_base_url || manifest?.endpoints?.ui || '').replace(/\/ui\/$/, '');
    } catch {
      // Ignore and keep fallback endpoint.
    }
  }

  async function loadManifest() {
    const normalized = (onboardingEndpoint || defaultOnboardingEndpoint()).replace(/\/$/, '');
    manifestUrl = `${normalized}/agent-onboarding.json`;
    try {
      const res = await fetch(manifestUrl);
      if (!res.ok) throw new Error(`Manifest fetch failed (${res.status})`);
      const manifest = await res.json();
      onboardingManifest = JSON.stringify(manifest, null, 2);
    } catch (err) {
      onboardingManifest = '';
      showToast(err instanceof Error ? err.message : 'Failed to load onboarding manifest');
    }
  }

  onMount(() => {
    const savedTab = window.localStorage.getItem(TAB_STORAGE_KEY);
    if (isTabName(savedTab)) {
      selectedTab = savedTab;
    }

    onboardingEndpoint = defaultOnboardingEndpoint();
    syncOnboardingEndpoint().finally(() => loadManifest());

    const params = new URLSearchParams(window.location.search);
    const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const grant = hashParams.get('oidc_grant') || params.get('oidc_grant');
    if (grant) {
      exchangeOidcGrant(grant)
        .then((res) => {
          void res;
          loginWithCookieSession();
          hashParams.delete('oidc_grant');
          params.delete('oidc_grant');
          const query = params.toString();
          const hash = hashParams.toString();
          window.history.replaceState(
            {},
            '',
            `${window.location.pathname}${query ? `?${query}` : ''}${hash ? `#${hash}` : ''}`
          );
        })
        .catch((err) => {
          showToast(err instanceof Error ? err.message : 'OIDC login failed');
        });
    }

    bootstrapAuth();

    const unsubscribe = authStore.subscribe(async ($auth) => {
      if ($auth.loggedIn) {
        startMatrixStream($auth.token ?? undefined);
        await Promise.all([refreshTree(), refreshPolicy(), refreshPolicyEvents(), initAi()]);
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(refreshActiveSession, 2500);
      } else {
        stopMatrixStream();
        if (pollTimer) clearInterval(pollTimer);
      }
    });
    return () => {
      unsubscribe();
      stopMatrixStream();
      if (pollTimer) clearInterval(pollTimer);
    };
  });

  onDestroy(() => {
    if (toastTimer) clearTimeout(toastTimer);
    if (pollTimer) clearInterval(pollTimer);
  });

  $: filteredMissions = (tree.missions ?? []).filter(
    (m: any) => !searchInput || m.name?.toLowerCase().includes(searchInput.toLowerCase())
  );
  $: transcript = buildTranscript(activeSession);
  $: pendingActions = (activeSession?.pending_actions ?? []).filter((a) => a.status === 'pending');
</script>

{#if $authStore.loggedIn}
  <div class="main-shell">
    <section class="tabs">
      <button class={`tab ${selectedTab === 'ai' ? 'active' : ''}`} on:click={() => setSelectedTab('ai')}>AI Console</button>
      <button class={`tab ${selectedTab === 'matrix' ? 'active' : ''}`} on:click={() => setSelectedTab('matrix')}>Matrix</button>
      <button class={`tab ${selectedTab === 'explorer' ? 'active' : ''}`} on:click={() => setSelectedTab('explorer')}>Explorer</button>
      <button class={`tab ${selectedTab === 'onboarding' ? 'active' : ''}`} on:click={() => setSelectedTab('onboarding')}>Onboarding</button>
      <button class={`tab ${selectedTab === 'governance' ? 'active' : ''}`} on:click={() => setSelectedTab('governance')}>Governance</button>
    </section>

    {#if selectedTab === 'ai'}
      <div class="glass-panel ai-shell">
        <div class="ai-header">
          <div>
            <h3>MissionControl AI Console</h3>
            <p class="muted">AI-first workspace. Reads auto-run, writes require approval.</p>
          </div>
          <div class="onboarding-actions">
            <button class="ghost" on:click={() => (showEventDebug = !showEventDebug)}>
              {showEventDebug ? 'Hide Debug Events' : 'Show Debug Events'}
            </button>
            <button class="ghost" on:click={newAiSession}>New Session</button>
          </div>
        </div>

        <div class="terminal-window" bind:this={terminalEl} on:scroll={onTranscriptScroll}>
          {#if transcript.length}
            {#each transcript as entry (entry.key)}
              <div class={`event-pill ${entry.kind === 'assistant' ? 'assistant-msg' : entry.kind === 'user' ? 'user-msg' : 'event-msg'}`}>
                <small>{entry.title} • {new Date(entry.createdAt).toLocaleTimeString()}</small>
                <p>{entry.body}</p>
                {#if entry.kind === 'event' && entry.payload && (showEventDebug || entry.eventType === 'tool_result')}
                  <details>
                    <summary>Details</summary>
                    <pre>{JSON.stringify(entry.payload, null, 2)}</pre>
                  </details>
                {/if}
              </div>
            {/each}
          {:else}
            <p class="muted">No events yet. Ask AI to list missions, inspect tasks, or explain capabilities.</p>
          {/if}
        </div>

        {#if !pinToBottom}
          <div class="jump-row">
            <button class="ghost" on:click={() => { pinToBottom = true; maybeScrollToBottom(true); }}>Jump to latest</button>
          </div>
        {/if}

        {#if pendingActions.length}
          <section class="grid" style="margin-top: 0.25rem;">
            {#each pendingActions as action}
              <article class="glass-panel">
                <strong>Approval Required</strong>
                <p class="muted">Tool: {action.tool}</p>
                <p class="muted">Reason: {action.reason || 'No reason provided'}</p>
                <details>
                  <summary>Arguments</summary>
                  <pre>{JSON.stringify(action.args, null, 2)}</pre>
                </details>
                <div class="onboarding-actions">
                  <button class="primary" on:click={() => approve(action.id)} disabled={aiBusy}>Approve</button>
                  <button class="ghost" on:click={() => reject(action.id)} disabled={aiBusy}>Reject</button>
                </div>
              </article>
            {/each}
          </section>
        {/if}

        <div class="composer">
          <textarea
            bind:value={aiInput}
            rows="3"
            placeholder="Ask MissionControl AI..."
            on:keydown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendAiMessage();
              }
            }}
          ></textarea>
          <button class="primary" on:click={sendAiMessage} disabled={aiBusy || !aiInput.trim()}>
            {aiBusy ? 'Running...' : 'Send'}
          </button>
        </div>
        {#if aiError}
          <p class="error">{aiError}</p>
        {/if}
      </div>
    {/if}

    {#if selectedTab === 'matrix'}
      <div class="glass-panel">
        <div class="grid">
          <div>
            <div class="status-chip">Matrix stream { $matrixStatus.connected ? 'live' : 'offline' }</div>
            <p class="muted">Rate limit: { $matrixStatus.rateLimit?.remaining ?? '—' } / { $matrixStatus.rateLimit?.limit ?? '—' }</p>
          </div>
          <div class="status-chip">Last event: {#if $lastEvent}{$lastEvent.time}{:else}waiting...{/if}</div>
        </div>
        <div class="matrix-timeline">
          {#each $eventChunks as chunk}
            <div class="event-pill">
              <small>{chunk.time}</small>
              <p>{chunk.label} - {chunk.status ?? 'pending'}</p>
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
            {#if lastRefreshed}<small class="muted">Updated {lastRefreshed}</small>{/if}
          </div>
          <input bind:value={searchInput} placeholder="Filter missions..." style="max-width:220px" />
        </div>
        <div class="grid">
          <section class="glass-panel">
            <h4>Missions {filteredMissions.length > 0 ? `(${filteredMissions.length})` : ''}</h4>
            <ul class="explorer-list">
              {#each filteredMissions as mission}
                <li>
                  <button class="ghost explorer-node-btn" on:click={() => selectMission(mission)}>
                    <span>{mission.name}</span>
                    <span class={`status-badge ${statusClass(mission.status)}`}>{mission.status ?? 'unknown'}</span>
                  </button>
                  {#if mission.klusters?.length}
                    <ul class="explorer-sublist">
                      {#each mission.klusters as kluster}
                        <li>
                          <button class="ghost explorer-subnode-btn" on:click={() => selectKluster(kluster)}>
                            <span>{kluster.name}</span>
                            <span class="muted">{kluster.task_count ?? kluster.recent_tasks?.length ?? 0} tasks</span>
                          </button>
                        </li>
                      {/each}
                    </ul>
                  {/if}
                </li>
              {:else}
                <li class="muted">No missions yet.</li>
              {/each}
            </ul>
          </section>
          <section class="glass-panel">
            <h4>Details</h4>
            {#if explorerBusy}
              <p class="muted">Loading node details...</p>
            {:else if selectedNodeData}
              {#if selectedNodeType === 'mission' && selectedNodeData.mission}
                <div class="explorer-detail-header">
                  <h4>{selectedNodeData.mission.name}</h4>
                  <span class={`status-badge ${statusClass(selectedNodeData.mission.status)}`}>{selectedNodeData.mission.status ?? 'unknown'}</span>
                </div>
                <p class="muted">{selectedNodeData.mission.description || 'No mission description.'}</p>
                <div class="detail-metrics">
                  <div class="status-chip">Klusters: {selectedNodeData.klusters?.length ?? 0}</div>
                  <div class="status-chip">Tasks: {selectedNodeData.mission.task_count ?? 0}</div>
                </div>
              {:else if selectedNodeType === 'kluster' && selectedNodeData.kluster}
                <div class="explorer-detail-header">
                  <h4>{selectedNodeData.kluster.name}</h4>
                  <span class={`status-badge ${statusClass(selectedNodeData.kluster.status)}`}>{selectedNodeData.kluster.status ?? 'unknown'}</span>
                </div>
                <p class="muted">{selectedNodeData.kluster.description || 'No kluster description.'}</p>
                <div class="detail-metrics">
                  <div class="status-chip">Sub-tasks: {selectedNodeData.tasks?.length ?? 0}</div>
                  <div class="status-chip">In Progress: {taskCountByStatus(selectedNodeData.tasks ?? [], 'in_progress')}</div>
                  <div class="status-chip">Blocked: {taskCountByStatus(selectedNodeData.tasks ?? [], 'blocked')}</div>
                </div>
                <div class="task-cards">
                  {#each selectedNodeData.tasks ?? [] as task}
                    <article class="task-card">
                      <div class="explorer-detail-header">
                        <strong>{task.title}</strong>
                        <span class={`status-badge ${statusClass(task.status)}`}>{task.status ?? 'unknown'}</span>
                      </div>
                      <p class="muted">{task.description || 'No description.'}</p>
                      <button class="ghost" on:click={() => selectTask(task)}>Open Task</button>
                    </article>
                  {:else}
                    <p class="muted">No sub-tasks for this kluster yet.</p>
                  {/each}
                </div>
              {:else if selectedNodeType === 'task' && selectedNodeData.task}
                <div class="explorer-detail-header">
                  <h4>{selectedNodeData.task.title}</h4>
                  <span class={`status-badge ${statusClass(selectedNodeData.task.status)}`}>{selectedNodeData.task.status ?? 'unknown'}</span>
                </div>
                <p class="muted">{selectedNodeData.task.description || 'No task description.'}</p>
                <pre>{JSON.stringify(selectedNodeData.task, null, 2)}</pre>
              {:else}
                <pre>{JSON.stringify(selectedNodeData, null, 2)}</pre>
              {/if}
            {:else if selectedNode}
              <pre>{JSON.stringify(selectedNode, null, 2)}</pre>
            {:else}
              <p class="muted">Choose a mission or kluster to inspect.</p>
            {/if}
          </section>
        </div>
      </div>
    {/if}

    {#if selectedTab === 'onboarding'}
      <div class="glass-panel">
        <h3>Agent Onboarding</h3>
        <label>Endpoint<input bind:value={onboardingEndpoint} placeholder="https://mc.example.com" /></label>
        <div class="onboarding-actions">
          <button class="ghost" on:click={loadManifest}>Regenerate Manifest</button>
          <button class="ghost" on:click={() => navigator.clipboard.writeText(onboardingManifest || '')}>Copy</button>
        </div>
        <div class="grid">
          <section class="glass-panel"><h4>Manifest URL</h4><code>{manifestUrl || 'fetch to generate'}</code></section>
          <section class="glass-panel"><h4>Manifest Preview</h4><pre>{onboardingManifest || 'No manifest yet.'}</pre></section>
        </div>
      </div>
    {/if}

    {#if selectedTab === 'governance'}
      <div class="glass-panel">
        <div class="grid">
          <section class="glass-panel"><h4>Active Policy</h4><pre>{policy ? JSON.stringify(policy, null, 2) : 'Loading...'}</pre></section>
          <section class="glass-panel">
            <h4>Policy Events</h4>
            <ul>
              {#each policyEvents as evt}
                <li class="muted">[{evt.level}] {evt.message}</li>
              {:else}
                <li>No events yet.</li>
              {/each}
            </ul>
          </section>
        </div>
      </div>
    {/if}

    {#if toastVisible && statusMessage}
      <div class="toast" role="alert">{statusMessage}</div>
    {/if}
  </div>
{:else}
  <section class="login">
    <div class="login-card">
      <div class="status-chip">MissionControl Secure</div>
      <h1>Team Console</h1>
      <p class="muted" style="margin:0;">OIDC is the production login path. Token login is for testing.</p>
      <div class="login-actions">
        <button class="primary" on:click={handleOidc}>Sign in via OIDC</button>
      </div>
      <label>Testing Token<input bind:value={initialToken} type="password" placeholder="MC_TOKEN" /></label>
      <div class="login-actions">
        <button class="ghost" on:click={handleToken}>Continue with token</button>
      </div>
    </div>
  </section>
{/if}
