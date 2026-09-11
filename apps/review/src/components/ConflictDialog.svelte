<script>
  /**
   * The 409 dialog: the server refused a review because the target moved under this client.
   *
   * The body is the service's `detail`: the reason (`stale-revision` or `retired`), the revision the
   * client edited from, the current revision and the current state of the target. Reapplying posts
   * the same change again from the revision the server just reported; discarding reloads the state.
   */
  let { session } = $props()

  const pending = $derived(session.conflict)
  const detail = $derived(pending?.detail ?? {})

  function state_rows(state) {
    if (!state || typeof state !== 'object') return []
    return Object.entries(state).filter(([, value]) => value !== null && value !== '')
  }

  function show(value) {
    if (value === null || value === undefined) return '—'
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
  }
</script>

{#if pending}
  <div class="overlay" role="presentation">
    <div class="dialog" role="dialog" aria-label="conflict">
      <h2>409 — {detail.error ?? 'conflict'}</h2>
      <p class="small">
        The change to <code>{pending.request.target_id}</code> (<code>{pending.request.field}</code>) was
        not recorded.
        {#if detail.error === 'stale-revision'}
          It was made from revision {detail.base_revision}, and the server is at revision {detail.revision}.
        {:else if detail.error === 'retired'}
          The unit has been retired by a split or a merge.
        {/if}
      </p>

      {#if detail.state}
        <h3 style="font-size:13px">the server's current state</h3>
        <table class="grid">
          <tbody>
            {#each state_rows(detail.state) as [field, value] (field)}
              <tr><th style="width:140px">{field}</th><td>{show(value)}</td></tr>
            {/each}
          </tbody>
        </table>
      {/if}

      <h3 style="font-size:13px">the change this client asked for</h3>
      <table class="grid">
        <tbody>
          <tr><th style="width:140px">target</th><td>{pending.request.target_type} <code>{pending.request.target_id}</code></td></tr>
          <tr><th>field</th><td><code>{pending.request.field}</code></td></tr>
          <tr><th>new</th><td>{show(pending.request.new)}</td></tr>
          <tr><th>base revision</th><td>{pending.request.base_revision ?? '—'}</td></tr>
        </tbody>
      </table>

      <div class="row" style="margin-top:12px">
        <button class="primary" onclick={() => session.reapplyConflict()}>
          reapply from revision {detail.revision ?? '—'}
        </button>
        <button onclick={() => session.discardConflict()}>discard and show the server's state</button>
      </div>
    </div>
  </div>
{/if}
