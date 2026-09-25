<script>
  import { onMount, tick } from 'svelte'
  import DrawnBoxDialog from '../components/DrawnBoxDialog.svelte'
  import ReferenceGlyph from '../components/ReferenceGlyph.svelte'
  import { pageList, pageRecord, drawBox } from '../lib/pages.js'
  import { t } from '../lib/i18n.svelte.js'
  // The page photos of the dataset, with every active box drawn over them. With Draw on, a drag on
  // the photo records a new box in page pixels; the box then asks for its character.
  let { clientId, pageId = '', inspect } = $props()
  let documents = $state(null), data = $state(null), error = $state(''), notice = $state('')
  let stage = $state(null), scale = $state(1), tx = $state(0), ty = $state(0), fitted = 1
  let drawing = $state(false), draft = $state(null), busy = $state(false), open = $state(null)
  let drag = null, closed = false, generation = 0
  const MIN_BOX = 3

  async function loadList() {
    error = ''
    try { const result = await pageList(); if (!closed) documents = result.documents }
    catch (e) { if (!closed) error = e.message }
  }
  async function loadPage(id, { keepView = false } = {}) {
    const current = ++generation
    error = ''
    try {
      const result = await pageRecord(id)
      if (closed || current !== generation) return null
      data = result
      if (!keepView) { await tick(); fit() }
      return result
    } catch (e) { if (!closed && current === generation) error = e.message }
    return null
  }

  function fit() {
    if (!stage || !data?.width) return
    const r = stage.getBoundingClientRect()
    fitted = Math.min(r.width / data.width, r.height / data.height)
    scale = fitted
    tx = (r.width - data.width * scale) / 2
    ty = (r.height - data.height * scale) / 2
  }
  function zoomAt(factor, cx, cy) {
    const next = Math.min(8, Math.max(fitted * 0.5, scale * factor))
    tx = cx - (cx - tx) * next / scale
    ty = cy - (cy - ty) * next / scale
    scale = next
  }
  function zoomCentre(factor) {
    const r = stage.getBoundingClientRect()
    zoomAt(factor, r.width / 2, r.height / 2)
  }
  function wheel(e) {
    e.preventDefault()
    const r = stage.getBoundingClientRect()
    zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top)
  }
  /** The pointer's place on the page, in the page pixels boxes are stored in. */
  function point(e) {
    const r = stage.getBoundingClientRect()
    return { x: Math.min(data.width, Math.max(0, (e.clientX - r.left - tx) / scale)),
             y: Math.min(data.height, Math.max(0, (e.clientY - r.top - ty) / scale)) }
  }
  function rectangle(a, b) {
    const x = Math.round(Math.min(a.x, b.x)), y = Math.round(Math.min(a.y, b.y))
    return { x, y, w: Math.round(Math.max(a.x, b.x)) - x, h: Math.round(Math.max(a.y, b.y)) - y }
  }

  function down(e) {
    if (e.button !== 0 || !data || busy) return
    stage.setPointerCapture(e.pointerId)
    if (drawing && data.drawable) { const start = point(e); drag = { kind: 'draw', start }; draft = rectangle(start, start) }
    else drag = { kind: 'pan', x: e.clientX, y: e.clientY, tx, ty, moved: false, target: e.target }
  }
  function move(e) {
    if (!drag) return
    if (drag.kind === 'draw') { draft = rectangle(drag.start, point(e)); return }
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true
    tx = drag.tx + dx; ty = drag.ty + dy
  }
  async function up() {
    const finished = drag
    drag = null
    if (!finished) return
    if (finished.kind === 'pan') {
      // A click on a box without a drag opens it.
      const id = !finished.moved && finished.target?.dataset?.unit
      if (id) choose(data.units.find(unit => unit.id === id))
      return
    }
    const box = draft
    draft = null
    if (!box || box.w < MIN_BOX || box.h < MIN_BOX) return
    busy = true
    try {
      const result = await drawBox(data.id, { box, client_id: clientId, idempotency_key: crypto.randomUUID() })
      await loadPage(data.id, { keepView: true })
      open = result.item
    } catch (e) { error = e.message }
    finally { busy = false }
  }

  function choose(unit) {
    if (!unit) return
    // A box that came with the dataset has its own reviewer; a drawn box opens the naming dialog.
    if (unit.manual) open = unit
    else inspect(unit.id, null, data.units.filter(item => item.character && !item.manual).map(item => ({ id: item.id })))
  }
  function changed(message) {
    notice = message
    setTimeout(() => { if (notice === message) notice = '' }, 2000)
    loadPage(data.id, { keepView: true })
  }
  function keys(e) {
    if (!data || open || e.target.closest?.('input,textarea,select,dialog')) return
    if (e.key === 'd' || e.key === 'D') { if (data.drawable) { drawing = !drawing; e.preventDefault() } }
    else if (e.key === '+' || e.key === '=') { zoomCentre(1.25); e.preventDefault() }
    else if (e.key === '-') { zoomCentre(0.8); e.preventDefault() }
    else if (e.key === '0') { fit(); e.preventDefault() }
    else if (e.key === 'Escape' && drag?.kind === 'draw') { drag = null; draft = null }
  }

  // Registered by hand because the wheel has to be non-passive to keep the page from scrolling.
  $effect(() => {
    const element = stage
    if (!element) return
    element.addEventListener('wheel', wheel, { passive: false })
    return () => element.removeEventListener('wheel', wheel)
  })
  $effect(() => {
    const id = pageId
    closed = false
    if (id) { data = null; drawing = false; loadPage(id) } else { data = null; loadList() }
  })
  onMount(() => {
    const resized = () => { if (data) fit() }
    window.addEventListener('resize', resized)
    return () => { closed = true; window.removeEventListener('resize', resized) }
  })
  // The code point names the character even where no font draws it, as with the 구결자 of the private-use area.
  const unitLabel = unit => unit.code_point || unit.character || t('pages.box.unidentified')
</script>

<svelte:window onkeydown={keys} />

{#if pageId}
  <section class="page-view">
    <div class="page-toolbar">
      <a class="quiet-link" href="#/pages">← {t('pages.back')}</a>
      {#if data}
        <strong class="page-title">{data.document_title ?? data.document_id} · {t('pages.pageNumber', { seq: data.seq })}</strong>
        <span class="toolbar-space"></span>
        {#if data.drawable}
          <button class="draw-toggle" class:active={drawing} aria-pressed={drawing} onclick={() => drawing = !drawing}
                  title={t('pages.draw.hint')}>{drawing ? t('pages.draw.on') : t('pages.draw.off')} <kbd>D</kbd></button>
        {:else}
          <span class="page-note">{t('pages.draw.unavailable')}</span>
        {/if}
        <div class="zoom-controls">
          <button aria-label={t('pages.zoom.out')} onclick={() => zoomCentre(0.8)}>−</button>
          <button aria-label={t('pages.zoom.fit')} onclick={fit}>{Math.round(scale * 100)}%</button>
          <button aria-label={t('pages.zoom.in')} onclick={() => zoomCentre(1.25)}>+</button>
        </div>
        <a class="quiet-link" class:disabled={!data.previous} aria-disabled={!data.previous} href={data.previous ? '#/pages/' + encodeURIComponent(data.previous) : undefined}>{t('pages.previous')}</a>
        <a class="quiet-link" class:disabled={!data.next} aria-disabled={!data.next} href={data.next ? '#/pages/' + encodeURIComponent(data.next) : undefined}>{t('pages.next')}</a>
      {/if}
    </div>
    {#if error}<div class="error-message" role="alert">{error}<button onclick={() => loadPage(pageId)}>{t('common.retry')}</button></div>{/if}
    {#if data}
      <div class="page-workspace">
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="page-stage" class:drawing={drawing && data.drawable} bind:this={stage}
             onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={() => { drag = null; draft = null }}>
          {#if data.image_url}
            <div class="page-sheet" style="width:{data.width}px;height:{data.height}px;transform:translate({tx}px,{ty}px) scale({scale})">
              <img src={data.image_url} alt={t('pages.photoAlt', { seq: data.seq })} draggable="false" />
              <svg viewBox="0 0 {data.width} {data.height}" aria-hidden="true">
                {#each data.units as unit (unit.id)}
                  {#if unit.box}
                    <rect data-unit={unit.id} class="unit-box" class:manual={unit.manual} class:unidentified={!unit.character}
                          x={unit.box.x} y={unit.box.y} width={unit.box.w} height={unit.box.h} vector-effect="non-scaling-stroke" />
                  {/if}
                {/each}
                {#if draft}<rect class="draft-box" x={draft.x} y={draft.y} width={draft.w} height={draft.h} vector-effect="non-scaling-stroke" />{/if}
              </svg>
            </div>
          {:else}
            <p class="page-note missing">{t('pages.photoMissing')}</p>
          {/if}
          {#if busy}<p class="stage-status" role="status">{t('common.saving')}</p>{/if}
        </div>
        <aside class="page-boxes">
          <h2>{t('pages.boxes.count', { count: data.units.length })}</h2>
          {#if data.drawable}<p class="page-note">{t('pages.draw.hint')}</p>{/if}
          {#if data.units.length}
            <ul>
              {#each data.units as unit (unit.id)}
                <li><button onclick={() => choose(unit)} class:manual={unit.manual}>
                  <span class="box-glyph">{#if unit.character}<ReferenceGlyph char={unit.character} code_point={unit.code_point ?? ''} size="sm" />{:else}<span class="box-none">?</span>{/if}</span>
                  <span class="box-name">{unitLabel(unit)}</span>
                  <small>{unit.manual ? t('pages.box.drawn') : t('pages.box.imported')}</small>
                </button></li>
              {/each}
            </ul>
          {/if}
        </aside>
      </div>
    {:else if !error}<p class="find-count" role="status">{t('pages.loading')}</p>{/if}
  </section>
  {#if open && data}<DrawnBoxDialog unit={open} page={data} {clientId} close={() => open = null} {changed} />{/if}
  {#if notice}<div class="save-toast" role="status">✓ {notice}</div>{/if}
{:else}
  <section class="explore pages-index">
    <h1 class="visually-hidden">{t('pages.heading')}</h1>
    {#if error}<div class="error-message" role="alert">{error}<button onclick={loadList}>{t('common.retry')}</button></div>{/if}
    {#if !documents && !error}<p class="find-count" role="status">{t('pages.loading')}</p>
    {:else if documents && !documents.length}<div class="empty"><span class="empty-mark">∅</span><h2>{t('pages.empty')}</h2></div>
    {:else if documents}
      {#each documents as document (document.id)}
        <article class="page-document">
          <h2>{document.title || document.id}</h2>
          <p class="page-note">{t('pages.document.pages', { count: document.pages.length })} · {t('pages.boxes.count', { count: document.units })}</p>
          <ol>
            {#each document.pages as page (page.id)}
              <li><a href={'#/pages/' + encodeURIComponent(page.id)}><span>{t('pages.pageNumber', { seq: page.seq })}</span>{#if page.units}<small>{page.units}</small>{/if}</a></li>
            {/each}
          </ol>
        </article>
      {/each}
    {/if}
  </section>
{/if}

<style>
  .page-view{display:flex;flex-direction:column;height:calc(100dvh - 89px);padding:0 4.4vw 16px}
  .page-toolbar{display:flex;align-items:center;gap:18px;padding:12px 0;flex-wrap:wrap;font-size:13px}
  .page-title{font-weight:500}
  .toolbar-space{flex:1}
  .quiet-link.disabled{opacity:.4;pointer-events:none}
  .draw-toggle{display:flex;align-items:center;gap:10px;font-size:12px;padding:8px 14px}
  .draw-toggle.active{background:var(--accent);border-color:var(--accent);color:#fff}
  .draw-toggle kbd{font:inherit;font-size:10px;border:1px solid currentColor;border-radius:3px;padding:1px 5px;opacity:.7}
  .zoom-controls{display:flex}
  .zoom-controls button{font-size:12px;padding:7px 11px;border-radius:0;min-width:36px;font-variant-numeric:tabular-nums}
  .zoom-controls button:first-child{border-radius:6px 0 0 6px}
  .zoom-controls button:last-child{border-radius:0 6px 6px 0}
  .zoom-controls button+button{margin-left:-1px}
  .page-workspace{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 240px;gap:16px}
  .page-stage{position:relative;overflow:hidden;background:#e7e7ea;border:1px solid var(--line);border-radius:8px;touch-action:none;cursor:grab;min-height:320px}
  .page-stage:active{cursor:grabbing}
  .page-stage.drawing{cursor:crosshair}
  .page-sheet{position:absolute;left:0;top:0;transform-origin:0 0}
  .page-sheet img{display:block;width:100%;height:100%;user-select:none;pointer-events:none;image-orientation:none}
  .page-sheet svg{position:absolute;inset:0;width:100%;height:100%}
  .unit-box{fill:#d0890014;stroke:#c07c00;stroke-width:1.5;cursor:pointer}
  .unit-box.manual{fill:#6356e51f;stroke:var(--accent);stroke-width:2}
  .unit-box.unidentified{stroke-dasharray:5 3}
  .page-stage.drawing .unit-box{pointer-events:none}
  .draft-box{fill:#6356e526;stroke:var(--accent);stroke-width:2;stroke-dasharray:4 3}
  .stage-status{position:absolute;left:12px;bottom:12px;background:#24212f;color:#fff;border-radius:20px;padding:6px 14px;font-size:12px}
  .page-note{font-size:12px;color:var(--muted)}
  .page-note.missing{padding:40px;text-align:center}
  .page-boxes{overflow:auto;border-left:1px solid var(--line);padding-left:16px}
  .page-boxes h2{font-size:13px;font-weight:500;margin-bottom:6px}
  .page-boxes ul{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:4px}
  .page-boxes li button{display:flex;align-items:center;gap:10px;width:100%;padding:6px 8px;border:1px solid transparent;background:transparent;text-align:left;font-size:12px}
  .page-boxes li button:hover{background:#f0f0f3}
  .box-glyph{width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:20px;background:#f4f4f5;border-radius:5px}
  .box-none{color:var(--muted)}
  .box-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .page-boxes small{color:var(--muted);font-size:10px}
  .page-boxes .manual small{color:var(--accent)}
  .pages-index{padding-bottom:40px}
  .page-document{border-top:1px solid var(--line);padding:18px 0}
  .page-document h2{font-size:17px;font-weight:500;margin-bottom:4px}
  .page-document ol{list-style:none;margin:12px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:6px}
  .page-document a{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:6px;padding:7px 11px;font-size:12px;background:#fff}
  .page-document a:hover{border-color:#a6a6ae}
  .page-document small{background:var(--accent-light);color:var(--accent);border-radius:10px;padding:1px 7px;font-size:10px}
  @media(max-width:700px){
    .page-view{height:calc(100dvh - 73px);padding:0 16px 12px}
    .page-workspace{grid-template-columns:1fr;grid-template-rows:minmax(0,1fr) auto}
    .page-boxes{border-left:0;padding-left:0;max-height:160px}
  }
</style>
