<script>
  /**
   * What this client has recorded: the events it posted, newest first, and the undo stack.
   *
   * There is no endpoint that lists another client's events; the log the server keeps is the SQLite
   * `events` table and `reviews.jsonl` after `atlas review apply`. This panel is the client's own
   * view of what it wrote, which is also what `z` walks back.
   */
  let { session } = $props()

  const top = $derived(session.undoStack[0]?.event_id ?? null)

  function show(value) {
    if (value === null || value === undefined) return '—'
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
  }
</script>

<div class="panel events">
  <h2>events of this client</h2>
  {#if !session.events.length}
    <p class="muted small" style="margin:0">
      nothing recorded yet. Every accept, reject, move, split, merge, reading and 字母 choice is a
      review event.
    </p>
  {:else}
    {#each session.events.slice(0, 40) as event (event.id)}
      <div class="event">
        <div class="row">
          <code>{event.id}</code>
          <span class="badge">{event.field}</span>
          {#if event.duplicate}<span class="badge warn">repeat</span>{/if}
          {#if event.id === top}
            <span style="flex:1"></span>
            <button class="small" onclick={() => session.undo()}>undo (z)</button>
          {/if}
        </div>
        <div class="small">
          <code>{event.target_id}</code> · rev {event.revision}
        </div>
        <div class="small muted">
          {show(event.old)} → <strong>{show(event.new)}</strong>
        </div>
        {#if event.created?.length}
          <div class="small muted">created {event.created.join(', ')}</div>
        {/if}
        {#if event.retired?.length}
          <div class="small muted">retired {event.retired.join(', ')}</div>
        {/if}
      </div>
    {/each}
  {/if}
</div>
