<script>
  import { request } from '../lib/client.js'
  import Glyph from './Glyph.svelte'
  import BoxEditor, { nudged } from './BoxEditor.svelte'
  import { t } from '../lib/i18n.svelte.js'
  import { toSource } from '../lib/cropBox.js'
  // `cropBox` is a box drawn over the crop (a redraw), in source pixels; the view stays framed on the
  // crop as it was cut. `editing` turns the box into one the reader moves and resizes here, and
  // `onedit` hears each new box, in source pixels, `onexit` an Escape.
  let { item, detail = null, corpus = false, cropBox = null, disabled = false, editing = false,
    onedit = null, onexit = null, describedby = undefined, onload = () => {}, onerror = () => {} } = $props()
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
  // The box shown and outlined, and the one the view is framed on: the cut crop, so a redraw moves
  // the outline and never the page.
  const crop = $derived(cropBox || data?.crop_box)
  const frame = $derived(data?.crop_box || cropBox)
  $effect(() => {
    if ((ready || cropReady) && notified !== 'loaded') { notified = 'loaded'; onload(item.id) }
    else if (!ready && !cropReady && !loading && cropFailed
      && (!contextual || (contextFailed && (!fullPage || fullFailed))) && notified !== 'failed') {
      notified = 'failed'; onerror(item.id)
    }
  })
  // Every crop opens on one rule: centred, its longer side about a third of the view's shorter one,
  // with the page around it. Nothing reframes it afterwards but the reader's own zoom and pan.
  const unit = $derived(frame ? Math.max(frame.w, frame.h) : 1)
  const baseScale = $derived(frame ? Math.min(Math.min(size.width, size.height) * .34 / unit, 8) : 1)
  const scale = $derived(baseScale * zoom)
  // Both images and the crop mask use source-image pixels, not the page's metadata scale.
  const origin = $derived(frame ? {
    x: size.width / 2 - (frame.x + frame.w / 2) * scale + pan.x,
    y: size.height / 2 - (frame.y + frame.h / 2) * scale + pan.y,
  } : { x: 0, y: 0 })
  // Until the page arrives, the crop itself stands where the page will put it: the same rule, written
  // for the stylesheet so it holds from the first paint, before any script measures the view. The box is
  // the record's, else a listing row's page box in source pixels; with neither, the image's own
  // proportions fill the rule's square.
  const known = $derived(data?.crop_box ?? item.crop_box ?? (item.box ? toSource(item, item.box) : null))
  const early = $derived.by(() => {
    if (!known) return ''
    const side = `min(34cqw, 34cqh, ${8 * Math.max(known.w, known.h)}px)`, longer = Math.max(known.w, known.h)
    return `width:calc(${side} * ${known.w / longer});height:calc(${side} * ${known.h / longer})`
  })
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
    const center = { x: frame.x + frame.w / 2, y: frame.y + frame.h / 2 }
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
    // While editing, the box editor takes a drag on the box or its handles; anywhere else still pans.
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
    // While editing, the arrows move the box a few screen pixels, and with Shift grow or shrink it from
    // its right and bottom; Escape leaves the editing.
    if (editing && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onexit?.(); return }
    if (editing && directions[event.key] && crop) {
      event.preventDefault(); event.stopPropagation()
      // A step is a few screen pixels, and never less than one page pixel, which is what a box is kept in.
      const page = data?.source_scale ?? [1, 1]
      onedit?.(nudged(crop, event, page.map(p => Math.max(3 / scale, p))), event.shiftKey ? 'resize' : 'move')
      return
    }
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
       role="application" aria-label={t('crop.viewer.label')} aria-describedby={editing ? describedby : undefined}
       aria-busy={loading} data-ready={ready} data-pan-x={pan.x} data-pan-y={pan.y} data-zoom={zoom}
       onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}
       onlostpointercapture={() => { pointer = null; dragging = false }} onkeydown={keydown}>
    <!-- One crop image from the first paint: where the page will put it once the record is in, and by
         the same rule before then, so neither the record nor the page around it moves it. -->
    {#if !ready && item.image && !cropFailed}<Glyph {item} class={known ? 'crop-early placed' : 'crop-early'} frame={early} eager
      alt={contextual ? '' : t('character.glyph.alt', { label: item.label })} draggable="false" onload={() => cropReady = true} onerror={() => cropFailed = true} />{/if}
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
        {#if editing}<BoxEditor box={crop} {scale} {origin} {disabled} onedit={(box, mode) => onedit?.(box, mode)}
          onstart={() => { expandPage = true; viewport.focus({ preventScroll: true }) }} />
        {:else}<span class="crop-mask" style={mask} aria-hidden="true"></span>{/if}
      {/if}
    {/if}
    {#if !loading && !ready && (detailFailed || !contextual || (contextFailed && (!fullPage || fullFailed)))}<span class="crop-only">{t('crop.only')}</span>{/if}
    <div class="crop-tools" aria-label={t('crop.tools.label')}>
      <button type="button" class="zoom-out" aria-label={t('crop.zoomOut')} title={t('crop.zoomOut')} disabled={disabled || !ready || zoom <= .6} onclick={() => magnify(1 / 1.25)}>−</button>
      <button type="button" class="reset-crop" aria-label={t('crop.returnToCrop')} title={t('crop.returnToCrop.title')} disabled={disabled || !ready} onclick={reset}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4H4v4m12-4h4v4M4 16v4h4m12-4v4h-4M9 9h6v6H9z"/></svg></button>
      <button type="button" class="zoom-in" aria-label={t('crop.zoomIn')} title={t('crop.zoomIn')} disabled={disabled || !ready || zoom >= 4} onclick={() => magnify(1.25)}>+</button>
    </div>
  </div>
  {#if data?.text}<div class="context-text"><span>{t('crop.transcription')}</span><p lang="ja">{data.text}</p></div>{/if}
</div>

<style>
  .crop-viewer{width:100%;min-width:0}
  /* Keep the scan canvas, shade and crop mask independent of the UI scheme. */
  .crop-viewport{height:380px;position:relative;container-type:size;overflow:clip;border-radius:12px;background:light-dark(#ebe8e3, #ebe8e3);isolation:isolate;touch-action:none;outline-offset:4px;user-select:none}
  .crop-viewport.ready{cursor:grab}
  .crop-viewport.dragging{cursor:grabbing}
  .crop-viewport:focus-visible{outline:2px solid var(--accent)}
  .crop-plane{position:absolute;left:0;top:0;transform-origin:0 0;pointer-events:none}
  .crop-plane img{position:absolute;display:block;max-width:none;max-height:none;object-fit:fill;filter:none;pointer-events:none}
  .page-photo{left:0;top:0}
  .context-shade{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;fill:light-dark(rgb(24 20 17 / 42%), rgb(24 20 17 / 42%))}
  .crop-mask{position:absolute;pointer-events:none;box-shadow:0 0 12px 3px light-dark(rgb(24 20 17 / 24%), rgb(24 20 17 / 24%))}
  /* The framing rule: centred, its longer side 34% of the view's shorter one. */
  .crop-viewport :global(.crop-early){position:absolute;left:50%;top:50%;width:min(34cqw,34cqh);height:min(34cqw,34cqh);translate:-50% -50%;max-width:none;max-height:none;filter:none;pointer-events:none}
  /* Placed, the crop's box is its rectangle on the page, which the image is drawn to fill. */
  .crop-viewport :global(.crop-early.placed){-webkit-mask:none;mask:none}
  .crop-viewport :global(.crop-early.placed img){object-fit:fill}
  .crop-tools{position:absolute;right:12px;bottom:12px;display:flex;gap:2px;background:light-dark(rgb(255 255 255 / 94%), rgb(27 27 31 / 94%));padding:3px;border-radius:8px;box-shadow:0 2px 12px light-dark(rgb(0 0 0 / 12%), rgb(0 0 0 / 40%));cursor:default}
  .crop-tools button{display:flex;align-items:center;justify-content:center;width:32px;height:32px;padding:0;border:0;border-radius:5px;background:transparent;font-size:22px;color:var(--ink);cursor:pointer}
  .crop-tools button:hover:enabled{background:var(--accent-light)}
  .crop-tools button:disabled{opacity:.35;cursor:default}
  .crop-tools svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.5}
  .crop-only{position:absolute;left:12px;bottom:18px;font-size:11px;color:var(--muted);background:light-dark(rgb(255 255 255 / 90%), rgb(27 27 31 / 90%));padding:4px 7px;border-radius:4px}
  .context-text{margin-top:10px;text-align:left;font-size:11px;color:var(--muted)}
  .context-text p{font-size:15px;line-height:1.8;max-height:7em;overflow:auto;overflow-wrap:anywhere;margin:4px 0 0;color:var(--ink)}
  @media(max-width:760px){.crop-viewport{height:300px}}
</style>
