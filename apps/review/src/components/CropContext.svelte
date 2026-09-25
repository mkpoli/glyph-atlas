<script>
  import { request } from '../lib/client.js'
  import Glyph from './Glyph.svelte'
  import { t } from '../lib/i18n.svelte.js'
  let { item, detail = null, corpus = false, cropBox = null, disabled = false,
    onload = () => {}, onerror = () => {} } = $props()
  let viewport = $state(null), data = $state(null), loading = $state(true), detailFailed = $state(false)
  let contextReady = $state(false), contextFailed = $state(false), fullReady = $state(false), fullFailed = $state(false)
  let expandPage = $state(false)
  let pageSize = $state({ width: 0, height: 0 }), size = $state({ width: 400, height: 380 })
  let zoom = $state(1), pan = $state({ x: 0, y: 0 }), dragging = $state(false)
  let cropReady = $state(false), cropFailed = $state(false), notified = $state(null)
  let pointer = null

  $effect(() => {
    const { id, revision, image_sha256, source_revision } = item
    const supplied = detail, fromCorpus = corpus
    const abort = new AbortController()
    data = null; loading = true; detailFailed = false
    contextReady = false; contextFailed = false; fullReady = false; fullFailed = fromCorpus
    expandPage = false
    cropReady = false; cropFailed = false; notified = null
    pageSize = { width: 0, height: 0 }; zoom = 1; pan = { x: 0, y: 0 }
    pointer = null; dragging = false
    if (supplied) {
      const sameSource = fromCorpus ? supplied.source_revision === source_revision : supplied.image_sha256 === image_sha256
      if (supplied.id === id && supplied.revision === revision && sameSource) data = supplied
      else detailFailed = true
      loading = false
      return () => abort.abort()
    }
    if (fromCorpus) { loading = false; detailFailed = true; return () => abort.abort() }
    request('/atlas/characters/' + encodeURIComponent(id), undefined, { signal: abort.signal })
      .then(result => {
        if (abort.signal.aborted) return
        // A context from another revision must never surround the crop being judged.
        if (result.revision === revision && result.image_sha256 === image_sha256) data = result
        else detailFailed = true
      })
      .catch(() => { if (!abort.signal.aborted) detailFailed = true })
      .finally(() => { if (!abort.signal.aborted) loading = false })
    return () => abort.abort()
  })
  $effect(() => {
    if (!viewport) return
    const observer = new ResizeObserver(entries => {
      const rect = entries[0].contentRect
      if (rect.width && rect.height) size = { width: rect.width, height: rect.height }
    })
    observer.observe(viewport)
    return () => observer.disconnect()
  })
  const contextual = $derived(Boolean((corpus || data?.context) && data?.context_box && data?.crop_box && data?.context_image))
  const fullPage = $derived(contextual && !corpus && data?.full_page_available !== false && (expandPage || contextFailed) && Boolean(data?.image_sha256))
  const ready = $derived(contextual && (fullReady || contextReady))
  const crop = $derived(cropBox || data?.crop_box)
  $effect(() => {
    if ((ready || cropReady) && notified !== 'loaded') { notified = 'loaded'; onload(item.id) }
    else if (!ready && !cropReady && !loading && cropFailed
      && (!contextual || (contextFailed && (!fullPage || fullFailed))) && notified !== 'failed') {
      notified = 'failed'; onerror(item.id)
    }
  })
  // The first view shows the whole context image: about five neighbours above and below the character
  // and three columns to each side, measured in its own size, so a reading can be checked against its line.
  const unit = $derived(crop ? Math.max(crop.w, crop.h) : 1)
  const baseScale = $derived(crop ? Math.min(size.width / (unit * 7), size.height / (unit * 11), 8) : 1)
  const scale = $derived(baseScale * zoom)
  // Both images and the crop mask use source-image pixels, not the page's metadata scale.
  const origin = $derived(crop ? {
    x: size.width / 2 - (crop.x + crop.w / 2) * scale + pan.x,
    y: size.height / 2 - (crop.y + crop.h / 2) * scale + pan.y,
  } : { x: 0, y: 0 })
  const transform = $derived(`translate(${origin.x}px, ${origin.y}px) scale(${scale})`)
  const mask = $derived(crop ? `left:${origin.x + crop.x * scale}px;top:${origin.y + crop.y * scale}px;width:${crop.w * scale}px;height:${crop.h * scale}px` : '')
  // A viewport-sized shade stays complete even when the crop is panned far off screen.
  const shadePath = $derived(crop ? `M0 0H${size.width}V${size.height}H0Z M${origin.x + crop.x * scale} ${origin.y + crop.y * scale}h${crop.w * scale}v${crop.h * scale}h${-crop.w * scale}Z` : '')

  function reset() {
    zoom = 1; pan = { x: 0, y: 0 }
    // Panning belongs to our transform. Keep native scroll state neutral as well,
    // including browsers recovering a focused descendant from an older render.
    if (viewport) { viewport.scrollLeft = 0; viewport.scrollTop = 0 }
  }
  function limitPan(next) {
    const bounds = fullReady ? { x: 0, y: 0, w: pageSize.width, h: pageSize.height } : data.context_box
    const center = { x: crop.x + crop.w / 2, y: crop.y + crop.h / 2 }
    // Keep some photograph in reach at an edge; reset always returns to the reviewed crop.
    const edge = 32
    return {
      x: Math.max(edge - size.width / 2 + (center.x - bounds.x - bounds.w) * scale,
        Math.min(size.width / 2 - edge + (center.x - bounds.x) * scale, next.x)),
      y: Math.max(edge - size.height / 2 + (center.y - bounds.y - bounds.h) * scale,
        Math.min(size.height / 2 - edge + (center.y - bounds.y) * scale, next.y)),
    }
  }
  function magnify(factor) {
    if (!ready || disabled) return
    expandPage = true
    const previous = zoom
    zoom = Math.max(.6, Math.min(4, zoom * factor))
    pan = limitPan({ x: pan.x * zoom / previous, y: pan.y * zoom / previous })
  }
  function down(event) {
    if (!ready || disabled || event.button !== 0 || event.target.closest('button')) return
    if (pointer) return
    expandPage = true
    event.preventDefault()
    viewport.focus({ preventScroll: true })
    pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, pan: { ...pan } }
    dragging = true
    viewport.setPointerCapture(event.pointerId)
  }
  function move(event) {
    if (!pointer || pointer.id !== event.pointerId) return
    pan = limitPan({ x: pointer.pan.x + event.clientX - pointer.x, y: pointer.pan.y + event.clientY - pointer.y })
  }
  function up(event) {
    if (!pointer || pointer.id !== event.pointerId) return
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId)
    pointer = null; dragging = false
  }
  function keydown(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return
    const directions = { ArrowLeft: [1, 0], ArrowRight: [-1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }
    // Consume viewer arrows even while loading, so they cannot advance the review queue.
    if (directions[event.key]) {
      event.preventDefault(); event.stopPropagation()
      if (ready && !disabled) {
        expandPage = true
        const [x, y] = directions[event.key], distance = event.shiftKey ? 96 : 32
        pan = limitPan({ x: pan.x + x * distance, y: pan.y + y * distance })
      }
    } else if (['Home', '+', '=', '-'].includes(event.key)) {
      event.preventDefault(); event.stopPropagation()
      if (event.key === 'Home') { if (!disabled) reset() }
      else magnify(event.key === '-' ? 1 / 1.25 : 1.25)
    }
    // Escape and Ctrl/Cmd+Enter keep their surrounding review actions.
  }
</script>

<div class="crop-viewer">
  <!-- This bounded image widget supplies arrow/Home/zoom keyboard controls alongside pointer panning. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <div class="crop-viewport" class:ready class:dragging bind:this={viewport} tabindex="0"
       role="application" aria-label={t('crop.viewer.label')}
       aria-busy={loading} data-ready={ready} data-pan-x={pan.x} data-pan-y={pan.y} data-zoom={zoom}
       onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}
       onlostpointercapture={() => { pointer = null; dragging = false }} onkeydown={keydown}>
    {#if !ready}<div class="crop-fallback" data-context-fallback>{#key item.image}<Glyph {item} eager onload={() => cropReady = true} onerror={() => cropFailed = true} />{/key}</div>{/if}
    {#if contextual}
      <div class="crop-plane" style={`transform:${transform}`} aria-hidden="true">
        {#if !fullReady && !contextFailed}<img class="context-photo" src={data.context_image} alt="" draggable="false"
          style={`left:${data.context_box.x}px;top:${data.context_box.y}px;width:${data.context_box.w}px;height:${data.context_box.h}px;visibility:${contextReady ? 'visible' : 'hidden'}`}
          onload={() => contextReady = true} onerror={() => contextFailed = true} />{/if}
        {#if fullPage && !fullFailed}<img class="page-photo" src={'/images/' + data.image_sha256} alt="" draggable="false" fetchpriority="low"
          style={`width:${pageSize.width}px;height:${pageSize.height}px;visibility:${fullReady ? 'visible' : 'hidden'}`}
          onload={event => { pageSize = { width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight }; fullReady = true }}
          onerror={() => fullFailed = true} />{/if}
      </div>
      {#if ready}
        <svg class="context-shade" viewBox={`0 0 ${size.width} ${size.height}`} preserveAspectRatio="none" aria-hidden="true"><path d={shadePath} fill-rule="evenodd" /></svg>
        <span class="crop-mask" style={mask} aria-hidden="true"></span>
      {/if}
    {/if}
    {#if !loading && !ready && (detailFailed || !contextual || (contextFailed && (!fullPage || fullFailed)))}<span class="crop-only">{t('crop.only')}</span>{/if}
    <div class="crop-tools" aria-label={t('crop.tools.label')}>
      <button type="button" class="zoom-out" aria-label={t('crop.zoomOut')} title={t('crop.zoomOut')} disabled={disabled || !ready || zoom <= .6} onclick={() => magnify(1 / 1.25)}>−</button>
      <button type="button" class="reset-crop" aria-label={t('crop.returnToCrop')} title={t('crop.returnToCrop.title')} disabled={disabled || !ready} onclick={reset}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4H4v4m12-4h4v4M4 16v4h4m12-4v4h-4M9 9h6v6H9z"/></svg></button>
      <button type="button" class="zoom-in" aria-label={t('crop.zoomIn')} title={t('crop.zoomIn')} disabled={disabled || !ready || zoom >= 4} onclick={() => magnify(1.25)}>+</button>
    </div>
  </div>
  {#if data?.text}<details class="context-text"><summary>{t('crop.transcription')}</summary><p lang="ja">{data.text}</p></details>{/if}
</div>

<style>
  .crop-viewer{width:100%;min-width:0}
  .crop-viewport{height:380px;position:relative;overflow:clip;border-radius:12px;background:#ebe8e3;isolation:isolate;touch-action:none;outline-offset:4px;user-select:none}
  .crop-viewport.ready{cursor:grab}
  .crop-viewport.dragging{cursor:grabbing}
  .crop-viewport:focus-visible{outline:2px solid var(--accent)}
  .crop-plane{position:absolute;left:0;top:0;transform-origin:0 0;pointer-events:none}
  .crop-plane img{position:absolute;display:block;max-width:none;max-height:none;object-fit:fill;filter:none;pointer-events:none}
  .page-photo{left:0;top:0}
  .context-shade{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;fill:rgb(24 20 17 / 42%)}
  .crop-mask{position:absolute;pointer-events:none;box-shadow:0 0 12px 3px rgb(24 20 17 / 24%)}
  .crop-fallback{position:absolute;inset:28px;display:flex;align-items:center;justify-content:center}
  .crop-fallback :global(img){width:100%;height:100%;object-fit:contain;filter:none}
  .crop-tools{position:absolute;right:12px;bottom:12px;display:flex;gap:2px;background:rgb(255 255 255 / 94%);padding:3px;border-radius:8px;box-shadow:0 2px 12px rgb(0 0 0 / 12%);cursor:default}
  .crop-tools button{display:flex;align-items:center;justify-content:center;width:32px;height:32px;padding:0;border:0;border-radius:5px;background:transparent;font-size:22px;color:var(--ink);cursor:pointer}
  .crop-tools button:hover:enabled{background:var(--accent-light)}
  .crop-tools button:disabled{opacity:.35;cursor:default}
  .crop-tools svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.5}
  .crop-only{position:absolute;left:12px;bottom:18px;font-size:11px;color:var(--muted);background:rgb(255 255 255 / 90%);padding:4px 7px;border-radius:4px}
  .context-text{margin-top:10px;text-align:left;font-size:11px;color:var(--muted)}
  summary{cursor:pointer}
  .context-text p{font-size:15px;line-height:1.8;max-height:7em;overflow:auto;overflow-wrap:anywhere;margin:8px 0}
  @media(max-width:760px){.crop-viewport{height:300px}}
</style>
