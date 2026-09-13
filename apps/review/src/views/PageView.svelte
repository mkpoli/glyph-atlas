<script>
  import Crop from '../components/Crop.svelte'
  import PageFeedback from '../components/PageFeedback.svelte'
  import api from '../lib/api.js'
  let { session } = $props()
  let availW = $state(0), zoom = $state(1), showBoxes = $state(true), siblings = $state([])
  const page = $derived(session.page)
  const volume = $derived(session.documents.find(d => d.id === page?.document_id))
  const drawing = $derived(session.mode === 'draw-line')
  const scale = $derived(page && availW ? Math.max(0.03, availW / page.width * zoom) : 0.2)
  const index = $derived(siblings.findIndex(p => p.id === page?.id))
  $effect(() => {
    const documentId = page?.document_id
    let cancelled = false
    if (documentId) api.pages({ document: documentId, limit: 2000 }).then(result => {
      if (!cancelled) siblings = result.items
    }).catch(() => {})
    return () => { cancelled = true }
  })
</script>

{#if page}<div class="page-reader workspace">
  <div class="reader-heading"><div><a class="small text-link" href="#/pages/{encodeURIComponent(page.document_id)}">← Back to volume</a><h1>{volume?.title || 'Source page'}</h1><p class="muted">{volume?.holder || ''}{volume?.shelfmark ? ` · ${volume.shelfmark}` : ''}</p></div><div class="page-navigation"><button disabled={index < 1} onclick={() => session.openPage(siblings[index - 1].id)} aria-label="Previous page">←</button><label>Page<select value={page.id} onchange={(e) => session.openPage(e.currentTarget.value)}>{#each siblings as sibling}<option value={sibling.id}>{sibling.seq + 1}</option>{/each}{#if !siblings.length}<option value={page.id}>{page.seq + 1}</option>{/if}</select></label><button disabled={index < 0 || index >= siblings.length - 1} onclick={() => session.openPage(siblings[index + 1].id)} aria-label="Next page">→</button></div></div>
  <div class="reader-columns">
    <button class="jump-to-text" onclick={() => document.querySelector('.feedback-pane')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Go to transcription and feedback ↓</button>
    <section class="scan-pane">
      <div class="scan-toolbar"><strong>Facsimile</strong><label class="small">Zoom <input aria-label="Scan zoom" type="range" min="0.5" max="2.5" step="0.1" bind:value={zoom} /></label><label class="small"><input type="checkbox" bind:checked={showBoxes} /> Line boxes</label><button onclick={() => session.beginDrawLine()} disabled={drawing || session.imageFailed}>Add line</button></div>
      {#if drawing}<p class="notice">Drag a rectangle around the missing line. Press Escape to cancel.</p>{/if}
      {#if session.imageFailed}<div class="empty-state"><h3>The scan could not be loaded</h3><p>Your transcription and feedback remain available.</p><a href={page.image_url} target="_blank" rel="noreferrer">Open the source image →</a></div>
      {:else}<div class="scan-scroll" bind:clientWidth={availW}>
        <Crop src={page.image_url} box={{ x: 0, y: 0, w: page.width, h: page.height }} {page} {scale} draw={drawing} minimum={12} oncreate={(box) => session.createLine(box)} onfail={() => session.markImageFailed()} title={`Page ${page.seq + 1}`}>
          {#if showBoxes}{#each session.lines.filter(line => line.box) as line (line.id)}<div class="box" class:ghost={line.role !== 'main'} style="left:{line.box.x * scale}px;top:{line.box.y * scale}px;width:{line.box.w * scale}px;height:{line.box.h * scale}px;pointer-events:{drawing ? 'none' : 'auto'}" role="button" tabindex="0" aria-label={`Review character boxes in line ${line.seq + 1}`} onpointerdown={(e) => e.stopPropagation()} onclick={() => session.openLine(line.id)} onkeydown={(e) => { if (e.key === 'Enter') session.openLine(line.id) }}><span class="box-label">{line.seq + 1}</span></div>{/each}{/if}
        </Crop>
      </div>{/if}
      <div class="scan-caption"><span>Page {page.seq + 1} · {session.lines.filter(line => line.box).length} mapped lines</span><a href={page.image_url} target="_blank" rel="noreferrer">Full image ↗</a></div>
      <details class="line-details"><summary>Character review by line</summary><div class="list">{#each session.lines as line}<button class="item" disabled={!line.box} onclick={() => session.openLine(line.id)}><span>{line.seq + 1}. {line.text || line.text_raw || 'Untranscribed line'}</span><small>{line.box ? `${line.units} characters` : 'No line box yet'}</small></button>{/each}</div></details>
    </section>
    {#key page.id}<PageFeedback {page} {session} />{/key}
  </div>
</div>{/if}
