<script>
  import { authStore, logout } from '$lib/auth';
  export let data;
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
      {#if $authStore.loggedIn}
        <button class="ghost" on:click={logout}>Logout</button>
      {/if}
    </div>
  </header>

  <slot />
</div>
