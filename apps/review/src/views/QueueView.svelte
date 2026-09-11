<script>
  /**
   * The queue: the lines the server hands out, and progress per document.
   *
   * Opening a line posts a `timing` event and leaves the queue view, which is what the calibration
   * protocol times; the progress columns come from `GET /documents`, whose `reviewed` count is the
   * number of active units a reviewer has touched.
   */
  let { session } = $props()

  const strategies = [
    { id: 'unreviewed', label: 'unreviewed' },
    { id: 'disagreement', label: 'disagreement' },
    { id: 'random', label: 'random' },
  ]

  const page = $derived(Math.floor(session.queue.offset / session.queue.limit) + 1)
  const pages = $derived(Math.max(1, Math.ceil(session.queue.total / session.queue.limit)))

  async function step(direction) {
    const offset = session.queue.offset + direction * session.queue.limit
    session.queue.offset = Math.max(0, Math.min(offset, Math.max(0, session.queue.total - 1)))
    await session.openQueue()
  }

  function percent(value) {
    return `${Math.round(value * 100)}%`
  }
</script>

<div class="stack">
  <div class="panel">
    <div class="row">
      <h2 style="margin:0">queue</h2>
      {#each strategies as strategy (strategy.id)}
        <button
          class={session.strategy === strategy.id ? 'primary' : ''}
          onclick={() => session.setStrategy(strategy.id)}
        >
          {strategy.label}
        </button>
      {/each}
      <span class="spacer" style="flex:1"></span>
      <label class="small muted">
        document
        <select
          value={session.documentFilter}
          onchange={(event) => session.setDocumentFilter(event.currentTarget.value)}
        >
          <option value="">all</option>
          {#each session.documents as document (document.id)}
            <option value={document.id}>{document.title || document.id}</option>
          {/each}
        </select>
      </label>
      <button onclick={() => session.openQueue()} disabled={session.queue.loading}>refresh</button>
    </div>
    <p class="small muted" style="margin:8px 0 0">
      {session.queue.total} line{session.queue.total === 1 ? '' : 's'} · page {page} of {pages} ·
      {session.clientId}
    </p>
  </div>

  <div class="panel">
    <h2>progress per document</h2>
    <table class="grid">
      <thead>
        <tr>
          <th>document</th>
          <th>pages</th>
          <th>lines</th>
          <th>units</th>
          <th>reviewed</th>
          <th style="width:140px">progress</th>
        </tr>
      </thead>
      <tbody>
        {#each session.progress as document (document.id)}
          <tr class:current={session.documentFilter === document.id}>
            <td>
              <button class="item" onclick={() => session.setDocumentFilter(document.id)}>
                {document.title || document.id}
              </button>
            </td>
            <td>{document.pages}</td>
            <td>{document.lines}</td>
            <td>{document.units}</td>
            <td>{document.reviewed}</td>
            <td>
              <div class="progress" title={percent(document.done)}>
                <span style="width:{percent(document.done)}"></span>
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <div class="panel">
    <h2>lines to review</h2>
    {#if session.queue.loading}
      <p class="muted">loading…</p>
    {:else if !session.queue.items.length}
      <p class="muted">no line matches this strategy.</p>
    {:else}
      <table class="grid">
        <thead>
          <tr>
            <th>line</th>
            <th>page</th>
            <th>units</th>
            <th>reviewed</th>
            <th>disagreements</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each session.queue.items as item (item.id)}
            <tr class="queue-row">
              <td>
                <button class="item" onclick={() => session.openLine(item.id)} title="open the line view">
                  {item.text_raw || item.text || '—'}
                  <div class="line-id">{item.id}</div>
                </button>
              </td>
              <td>
                <button class="item" onclick={() => session.openPage(item.page_id)}>
                  <span class="line-id">{item.page_id}</span>
                </button>
              </td>
              <td>{item.units}</td>
              <td>{item.reviewed}</td>
              <td>
                {#if item.disagreements}
                  <span class="badge warn">{item.disagreements}</span>
                {:else}
                  <span class="muted">0</span>
                {/if}
              </td>
              <td>
                <button onclick={() => session.openLine(item.id)}>review</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
      <div class="row" style="margin-top:8px">
        <button onclick={() => step(-1)} disabled={session.queue.offset === 0}>← previous</button>
        <button onclick={() => step(1)} disabled={session.queue.offset + session.queue.limit >= session.queue.total}>
          next →
        </button>
      </div>
    {/if}
  </div>
</div>
