<script>
  /**
   * The page view: the page image with the line boxes the detector produced.
   *
   * A click on a box opens the line view. `l` (or the button) starts drawing a line the detector
   * missed: drag a rectangle, and `POST /lines` records it. A page whose image is not in the local
   * cache shows the IIIF URL it would be fetched from and offers to skip the page.
   */
  import Crop from '../components/Crop.svelte'

  let { session } = $props()

  let canvas = $state(null)
  let availW = $state(0)
  let availH = $state(0)

  const page = $derived(session.page)
  const uncached = $derived(page ? !page.sha256 : false)
  const drawing = $derived(session.mode === 'draw-line')

  const scale = $derived.by(() => {
    if (!page || !availW) return 0.2
    const byWidth = availW / page.width
    const byHeight = availH ? availH / page.height : byWidth
    return Math.max(0.05, Math.min(byWidth, byHeight, 1.5))
  })

  function percent(value) {
    return `${Math.round(value * 100)}%`
  }
</script>

{#if page}
  <div class="page-grid" style="display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:16px;align-items:start">
    <div class="stack">
      <div class="panel">
        <div class="row">
          <button onclick={() => session.openQueue()}>← queue</button>
          <strong>{page.id}</strong>
          <span class="muted small">{page.document_id} · seq {page.seq}</span>
          <span class="badge">{page.lines} lines</span>
          <span class="badge">{page.units} units</span>
          <span class="badge ok">{page.reviewed} reviewed</span>
          {#if uncached}
            <span class="badge warn">image not in the local cache</span>
          {/if}
          <span style="flex:1"></span>
          <button class="primary" onclick={() => session.beginDrawLine()} disabled={drawing || session.imageFailed}>
            new line (l)
          </button>
        </div>
        {#if drawing}
          <p class="small" style="margin:8px 0 0">
            drag a rectangle over the page for the line the detector missed; <kbd>escape</kbd> cancels.
          </p>
        {/if}
      </div>

      {#if session.imageFailed}
        <div class="unavailable">
          <h3 style="margin-top:0">the page image could not be loaded</h3>
          <p class="small">
            The page is skipped. The image this page names is not in <code>cache/images</code>, and the
            upstream URL did not answer. Fetch it with <code>atlas images fetch</code>, or open it
            directly:
          </p>
          <p class="small"><a href={page.image_url} target="_blank" rel="noreferrer">{page.image_url}</a></p>
          <div class="row">
            <button class="primary" onclick={() => session.skipPage()}>skip this page</button>
            <button onclick={() => session.openQueue()}>back to the queue</button>
          </div>
        </div>
      {:else}
        {#if uncached}
          <div class="panel small">
            <strong>not cached:</strong>
            <a href={page.image_url} target="_blank" rel="noreferrer">{page.image_url}</a>
            <button style="margin-left:8px" onclick={() => session.skipPage()}>skip this page</button>
          </div>
        {/if}
        <div
          class="page-canvas"
          bind:this={canvas}
          bind:clientWidth={availW}
          bind:clientHeight={availH}
          style="max-height:calc(100vh - 260px);overflow:auto;display:flex;justify-content:center"
        >
          <Crop
            src={page.image_url}
            box={{ x: 0, y: 0, w: page.width, h: page.height }}
            page={page}
            {scale}
            draw={drawing}
            minimum={12}
            oncreate={(box) => session.createLine(box)}
            onfail={() => session.markImageFailed()}
            title={`${page.id} (${page.width}×${page.height})`}
          >
            {#each session.lines as line (line.id)}
              <div
                class="box {session.line?.id === line.id ? 'selected' : ''}"
                class:ghost={line.role !== 'main'}
                style="left:{line.box.x * scale}px;top:{line.box.y * scale}px;width:{line.box.w * scale}px;height:{line.box.h * scale}px;pointer-events:{drawing ? 'none' : 'auto'}"
                role="button"
                tabindex="0"
                title={`${line.id} · ${line.box.w}×${line.box.h} · ${line.units} units · rev ${line.revision}`}
                onpointerdown={(event) => event.stopPropagation()}
                onclick={() => session.openLine(line.id)}
                onkeydown={(event) => {
                  if (event.key === 'Enter') session.openLine(line.id)
                }}
              >
                <span
                  class="badge"
                  style="position:absolute;left:1px;top:1px;font-size:10px;padding:0 4px;background:var(--surface)">{line.seq}</span
                >
              </div>
            {/each}
          </Crop>
        </div>
      {/if}
    </div>

    <div class="panel">
      <h2>lines of the page</h2>
      <div class="list">
        {#each session.lines as line (line.id)}
          <button
            class="item {session.line?.id === line.id ? 'current' : ''}"
            onclick={() => session.openLine(line.id)}
          >
            <div class="tagline">{line.text_raw || line.text || '—'}</div>
            <div class="line-id">{line.id}</div>
            <div class="small muted">
              seq {line.seq} · {line.units} units · rev {line.revision}
              {#if line.role !== 'main'}· {line.role}{/if}
              {#if line.match_confidence != null}· {percent(line.match_confidence)}{/if}
            </div>
          </button>
        {/each}
      </div>
    </div>
  </div>
{/if}
