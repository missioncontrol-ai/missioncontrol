<script>
  import { onMount } from 'svelte';
  import '../app.css';
  import { authStore, logout } from '$lib/auth';

  let theme = 'dark';

  function applyTheme(next) {
    theme = next;
    if (typeof document !== 'undefined') {
      document.documentElement.dataset.theme = next;
      localStorage.setItem('missioncontrol:theme', next);
    }
  }

  function toggleTheme() {
    applyTheme(theme === 'dark' ? 'light' : 'dark');
  }

  onMount(() => {
    const saved = localStorage.getItem('missioncontrol:theme');
    applyTheme(saved === 'light' ? 'light' : 'dark');
  });
</script>

<div class="shell">
  <header class="shell-header glass-panel">
    <div>
      <div class="status-chip">MissionControl</div>
      <p style="margin:0.25rem 0 0;font-size:0.9rem; color: var(--muted);">
        {#if $authStore.loggedIn}
          Connected user token
        {:else}
          Authenticate to continue
        {/if}
      </p>
    </div>
    <div class="header-actions">
      <button class="ghost icon-btn" on:click={toggleTheme} title={theme === 'dark' ? 'Switch to light' : 'Switch to dark'}>
        {theme === 'dark' ? '☀' : '☾'}
      </button>
      {#if $authStore.loggedIn}
        <button class="ghost" on:click={logout}>Logout</button>
      {/if}
    </div>
  </header>

  <slot />
</div>
