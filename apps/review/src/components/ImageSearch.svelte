<script>
  // Search by image: the reader gives an image (dropped, pasted, chosen or photographed), draws the box
  // around one character, and the classifier, run in this browser, names the likeliest characters and
  // computes the vector the site searches with. The image never leaves the device.
  //
  // The model is downloaded only when the reader presses the button that names its size, and is kept
  // in this browser; a newer one the site publishes is offered, never fetched by itself.
  import { onMount, untrack } from 'svelte'
  import BoxEditor, { nudged } from './BoxEditor.svelte'
  import Glyph from './Glyph.svelte'
  import ScriptText from './ScriptText.svelte'
  import ScriptLine from './ScriptLine.svelte'
  import ScriptLegend from './ScriptLegend.svelte'
  import { cropDetails } from '../lib/cropDetails.js'
  import { datingLine, tileDate } from '../lib/dating.js'
  import { isUnassigned } from '../lib/identity.js'
  import { t, formatNumber } from '../lib/i18n.svelte.js'
  import { offered, installed, download, remove, storage, load, embed, candidatesOf, search, downloadSize, supported, RUNTIME_VERSION } from '../lib/imageSearch.js'

  // `given` is `{ file, key }`: an image the search box was given, opened each time `key` changes, so a
  // second image goes to the panel already open. `inspect` opens a crop in the inspector.
  let { given = null, onclose = () => {}, inspect } = $props()

  let info = $state(null), infoFailed = $state(false), kept = $state(null), persisted = $state(false)
  let progress = $state(null), downloadError = $state(''), aborter = null
  let image = $state(null), imageError = $state(''), box = $state(null), stage = $state(null), stageSize = $state({ width: 0, height: 0 })
  let preview = $state(null), running = $state(false), runError = $state(''), result = $state(null), searchedBox = $state(null)
  let chosen = $state(0), tab = $state('candidates'), dragOver = $state(false), picker = $state(null)
  // The decoded image, kept as a bitmap: only the boxed part is ever drawn to a canvas. `opening` and
  // `runs` count the images opened and the searches started, so a slower, older one never lands last.
  let bitmap = null, opening = 0, runs = 0, querying = null

  const megabytes = bytes => t('imageSearch.megabytes', { size: formatNumber(Math.max(1, Math.round(bytes / 1e6))) })
  const size = $derived(downloadSize(info))
  // A kept model is out of date when the site publishes another version, or when this page runs another
  // onnxruntime-web than the one kept with it. Until the reader updates, the panel offers the update.
  const outdated = $derived(Boolean(kept && info?.model && (kept.version !== info.model.version || kept.runtime !== RUNTIME_VERSION)))
  const offerable = $derived(Boolean(info?.model && info?.runtime && info?.ready))
  const usable = $derived(Boolean(kept && info?.ready && !outdated))

  // Counts removals, so a refresh that read the cache before one never brings the removed model back.
  let removals = 0
  async function refresh() {
    const at = removals
    infoFailed = false
    try { info = await offered() } catch { infoFailed = true }
    const found = await installed().catch(() => null)
    if (at === removals) kept = found
    persisted = (await storage()).persisted
  }

  async function fetchModel() {
    downloadError = ''
    aborter = new AbortController()
    progress = { received: 0, total: size }
    try {
      kept = await download(info, (received, total) => progress = { received, total }, aborter.signal)
      persisted = (await storage()).persisted
    } catch (error) {
      if (error.name !== 'AbortError') downloadError = t('imageSearch.download.failed')
    } finally { progress = null; aborter = null }
  }

  async function removeModel() {
    // A search still running would otherwise show its answer after the model is gone, and the panel
    // stops offering a search at once, before the files are deleted.
    querying?.abort(); runs++; removals++; running = false
    kept = null; result = null; preview = null; searchedBox = null; runError = ''
    await remove()
  }

  // -- the image -------------------------------------------------------------------------------------

  async function open(file) {
    if (!usable || !file) return
    const ticket = ++opening
    querying?.abort(); runs++
    imageError = ''; result = null; runError = ''; preview = null; searchedBox = null
    if (!file.type?.startsWith('image/')) { imageError = t('imageSearch.image.notImage'); return }
    try {
      // The photo's own pixel values, as Pillow reads them: no colour-profile conversion. Its
      // orientation is applied, as the image on screen shows it.
      const decoded = await createImageBitmap(file, { imageOrientation: 'from-image', colorSpaceConversion: 'none', premultiplyAlpha: 'none' })
      if (ticket !== opening) { decoded.close(); return }
      bitmap?.close()
      if (image?.url) URL.revokeObjectURL(image.url)
      bitmap = decoded
      image = { url: URL.createObjectURL(file), width: decoded.width, height: decoded.height }
      // The box starts on the middle of the image; the reader moves it onto the character.
      box = { x: Math.round(image.width * .2), y: Math.round(image.height * .2), w: Math.round(image.width * .6), h: Math.round(image.height * .6) }
    } catch { if (ticket === opening) imageError = t('imageSearch.image.unreadable') }
  }

  /**
   * The boxed part of the image as RGBA. A box of more than `LARGEST` pixels a side is drawn smaller
   * first: the model sees 128, and a whole phone photo would not fit some browsers' canvases.
   */
  const LARGEST = 2048
  function boxed({ x, y, w, h }) {
    const shrink = Math.min(1, LARGEST / Math.max(w, h)), width = Math.max(1, Math.round(w * shrink)), height = Math.max(1, Math.round(h * shrink))
    const canvas = new OffscreenCanvas(width, height), context = canvas.getContext('2d', { willReadFrequently: true })
    context.drawImage(bitmap, x, y, w, h, 0, 0, width, height)
    return { pixels: context.getImageData(0, 0, width, height).data, width, height }
  }

  // A paste into a text field is the field's: only one onto the page itself opens an image here. The
  // search box hands its own pasted image over through `given`.
  // The event's own target, inside a shadow root too; an input that takes no text is no text field.
  const TEXT_INPUTS = new Set(['', 'text', 'search', 'url', 'tel', 'email', 'password', 'number'])
  const typing = target => target instanceof HTMLTextAreaElement || (target instanceof HTMLElement && target.isContentEditable)
    || (target instanceof HTMLInputElement && TEXT_INPUTS.has(target.type))
  function pasted(event) {
    if (typing(event.composedPath?.()[0] ?? event.target)) return
    const found = [...(event.clipboardData?.items ?? [])].find(item => item.kind === 'file' && item.type.startsWith('image/'))
    if (!found || !usable) return
    event.preventDefault()
    open(found.getAsFile())
  }

  function dropped(event) {
    event.preventDefault(); dragOver = false
    if (!usable) return
    const found = [...(event.dataTransfer?.files ?? [])].find(f => f.type.startsWith('image/'))
    if (found) open(found); else imageError = t('imageSearch.image.notImage')
  }

  $effect(() => {
    if (!stage) return
    const observer = new ResizeObserver(entries => { const r = entries[0].contentRect; stageSize = { width: r.width, height: r.height } })
    observer.observe(stage)
    return () => observer.disconnect()
  })
  const scale = $derived(image && stageSize.width ? Math.min(stageSize.width / image.width, stageSize.height / image.height) : 1)
  const origin = $derived(image ? { x: (stageSize.width - image.width * scale) / 2, y: (stageSize.height - image.height * scale) / 2 } : { x: 0, y: 0 })

  /** A box kept inside the image, in whole pixels, at least two each way. */
  function bounded({ x, y, w, h }, mode) {
    if (mode === 'move') {
      w = Math.round(w); h = Math.round(h)
      return { x: Math.round(Math.max(0, Math.min(x, image.width - w))), y: Math.round(Math.max(0, Math.min(y, image.height - h))), w, h }
    }
    x = Math.round(Math.max(0, Math.min(x, image.width - 2))); y = Math.round(Math.max(0, Math.min(y, image.height - 2)))
    return { x, y, w: Math.round(Math.max(2, Math.min(w, image.width - x))), h: Math.round(Math.max(2, Math.min(h, image.height - y))) }
  }
  const edit = (next, mode) => { box = bounded(next, mode) }
  function keydown(event) {
    if (!box || running || event.ctrlKey || event.metaKey || event.altKey) return
    const step = Math.max(1, 3 / scale)
    const next = nudged(box, event, [step, step])
    if (!next) return
    event.preventDefault()
    edit(next, event.shiftKey ? 'resize' : 'move')
  }
  const changed = $derived(Boolean(box && (!searchedBox || ['x', 'y', 'w', 'h'].some(k => box[k] !== searchedBox[k]))))

  // -- the search ------------------------------------------------------------------------------------

  async function run() {
    if (!usable || !box || !bitmap || running) return
    const ticket = ++runs, asked = { ...box }
    querying = new AbortController()
    running = true; runError = ''
    try {
      const model = await load(kept)
      const { pixels, width, height } = boxed(asked)
      const { probs, features, grey } = await embed(model, pixels, width, height)
      if (ticket !== runs) return
      preview = grey
      const { candidates, other } = candidatesOf(model.classes, probs)
      const found = await search(model.encoder, features, candidates, querying.signal)
      if (ticket !== runs) return
      searchedBox = asked
      result = { candidates: candidates.map((c, n) => ({ ...c, crops: found.candidates[n]?.crops ?? [] })), other, similar: found.similar }
      chosen = 0
      tab = candidates.length ? 'candidates' : 'similar'
    } catch (error) {
      if (ticket !== runs || error.name === 'AbortError') return
      runError = error.status === 429 ? t('imageSearch.search.tooMany') : error.status === 409 ? t('imageSearch.search.outdated')
        : error.status === 503 ? t('imageSearch.unavailable') : t('imageSearch.search.failed')
      if (error.status === 409) await refresh()
    } finally { if (ticket === runs) running = false }
  }

  // The grey square the model saw, drawn small beside the box.
  let previewCanvas = $state(null)
  $effect(() => {
    if (!previewCanvas || !preview) return
    const side = Math.sqrt(preview.length), data = new ImageData(side, side)
    for (let i = 0; i < preview.length; i++) { data.data[i * 4] = data.data[i * 4 + 1] = data.data[i * 4 + 2] = preview[i]; data.data[i * 4 + 3] = 255 }
    previewCanvas.width = side; previewCanvas.height = side
    previewCanvas.getContext('2d').putImageData(data, 0, 0)
  })

  const shown = $derived(!result ? [] : tab === 'similar' ? result.similar : result.candidates[chosen]?.crops ?? [])
  const percent = value => `${formatNumber(Math.round(value * 100))}%`
  const label = item => isUnassigned(item) ? t('corpus.unassigned') : item.label ?? ''
  // The work and its holder, as a tile names them; the date is the year beside the score, as on Explore's tiles.
  const sourceLines = item => { const dating = datingLine(item); return cropDetails(item).filter(line => line !== dating).slice(-2) }
  function openCrop(item) {
    const origin = entry => entry.origin === 'corpus' ? 'corpus' : 'collection'
    inspect(item.id, null, shown.map(entry => ({ ...entry, origin: origin(entry) })), null, origin(item))
  }

  // An image the search box was given opens once the kept model is known to be usable.
  let openedKey = null
  $effect(() => {
    const request = given
    if (!usable || !request?.file || request.key === openedKey) return
    openedKey = request.key
    untrack(() => open(request.file))
  })

  onMount(() => {
    refresh()
    return () => { aborter?.abort(); querying?.abort(); bitmap?.close(); if (image?.url) URL.revokeObjectURL(image.url) }
  })
</script>

<svelte:window onpaste={pasted} />

<section class="image-search" aria-labelledby="image-search-title" class:drag-over={dragOver}
         ondragover={e => { e.preventDefault(); dragOver = true }} ondragleave={e => { if (!e.currentTarget.contains(e.relatedTarget)) dragOver = false }} ondrop={dropped}>
  <header class="image-search-header">
    <h2 id="image-search-title">{t('imageSearch.title')}</h2>
    <button type="button" class="icon-button" aria-label={t('imageSearch.close')} onclick={onclose}>×</button>
  </header>

  <div class="model-panel">
    {#if !supported()}<p class="model-note">{t('imageSearch.unsupported')}</p>
    {:else if infoFailed}<p class="model-note">{t('imageSearch.infoFailed')} <button type="button" class="quiet-link" onclick={refresh}>{t('common.retry')}</button></p>
    {:else if !info}<p class="model-note">{t('imageSearch.checking')}</p>
    {:else if !usable && !offerable}<p class="model-note" role="status">{t('imageSearch.unavailable')}</p>
    {:else if progress}
      <div class="model-progress">
        <progress max={progress.total} value={progress.received} aria-labelledby="image-search-progress"></progress>
        <span id="image-search-progress">{t('imageSearch.download.progress', { received: megabytes(progress.received), total: megabytes(progress.total) })}</span>
        <button type="button" class="quiet-link" onclick={() => aborter?.abort()}>{t('imageSearch.download.cancel')}</button>
      </div>
    {:else if !kept}
      <p class="model-explain">{t('imageSearch.explain')}</p>
      <p class="model-privacy">{t('imageSearch.privacy')}</p>
      <div class="model-actions">
        <button type="button" class="primary" onclick={fetchModel}>{t('imageSearch.download', { size: megabytes(size) })}</button>
        <small>{t('imageSearch.download.kept')}</small>
      </div>
    {:else}
      <div class="model-status" role="status">
        <span>{[t('imageSearch.model.kept', { size: megabytes(kept.bytes), version: kept.version.slice(0, 8) }), persisted ? t('imageSearch.model.persisted') : null].filter(Boolean).join(' · ')}</span>
        {#if outdated && offerable}<button type="button" class="update" onclick={fetchModel}>{t('imageSearch.model.update', { size: megabytes(size) })}</button>{/if}
        <button type="button" class="quiet-link" onclick={removeModel}>{t('imageSearch.model.remove')}</button>
      </div>
    {/if}
    {#if downloadError}<p class="model-error" role="alert">{downloadError}</p>{/if}
    {#if !usable && imageError}<p class="model-error" role="alert">{imageError}</p>{/if}
  </div>

  {#if usable}
    {#if !image}
      <div class="image-drop">
        <p>{t('imageSearch.image.prompt')}</p>
        <button type="button" onclick={() => picker?.click()}>{t('imageSearch.image.choose')}</button>
        <p class="model-privacy">{t('imageSearch.privacy')}</p>
      </div>
    {:else}
      <div class="image-work">
        <div class="image-stage-wrap">
          <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
          <div class="image-stage" bind:this={stage} tabindex="0" role="application" aria-label={t('imageSearch.box.label')} aria-describedby="image-search-keys" onkeydown={keydown}>
            <img src={image.url} alt="" draggable="false" style={`left:${origin.x}px;top:${origin.y}px;width:${image.width * scale}px;height:${image.height * scale}px`} />
            {#if box}<BoxEditor {box} {scale} {origin} onedit={edit} onstart={() => stage?.focus({ preventScroll: true })} disabled={running} />{/if}
          </div>
          <p class="image-keys" id="image-search-keys">{t('imageSearch.box.keys')}</p>
        </div>
        <div class="image-side">
          {#if preview}<figure class="model-view"><canvas bind:this={previewCanvas} aria-hidden="true"></canvas><figcaption>{t('imageSearch.box.seen')}</figcaption></figure>{/if}
          <button type="button" class="primary" disabled={running || !changed} onclick={run}>{running ? t('imageSearch.search.running') : result ? t('imageSearch.search.again') : t('imageSearch.search')}</button>
          <button type="button" class="quiet-link" disabled={running} onclick={() => picker?.click()}>{t('imageSearch.image.another')}</button>
          <p class="model-privacy">{t('imageSearch.privacy')}</p>
        </div>
      </div>
    {/if}
    {#if imageError}<p class="model-error" role="alert">{imageError}</p>{/if}
    {#if runError}<p class="model-error" role="alert">{runError}</p>{/if}
    <input class="visually-hidden" type="file" accept="image/*" bind:this={picker} tabindex="-1" aria-hidden="true"
           onchange={e => { const chosenFile = e.currentTarget.files?.[0]; e.currentTarget.value = ''; if (chosenFile) open(chosenFile) }} />
  {/if}

  {#if result}
    <div class="image-results">
      <div class="result-tabs" role="group">
        <button type="button" aria-pressed={tab === 'candidates'} disabled={!result.candidates.length} onclick={() => tab = 'candidates'}>{t('imageSearch.tab.candidates')}</button>
        <button type="button" aria-pressed={tab === 'similar'} onclick={() => tab = 'similar'}>{t('imageSearch.tab.similar')}</button>
      </div>
      {#if tab === 'candidates'}
        <div class="candidate-chips" role="group" aria-label={t('imageSearch.candidates.label')}>
          {#each result.candidates as candidate, n (candidate.key)}
            <button type="button" aria-pressed={chosen === n} onclick={() => chosen = n}>
              <span class="chip-chars"><ScriptText text={candidate.chars.slice(0, 3).join('')} /></span>
              <small>{percent(candidate.score)}{#if candidate.family} · {t('imageSearch.candidates.family')}{/if}</small>
            </button>
          {/each}
        </div>
        {#if result.other >= .2}<p class="model-note">{t('imageSearch.candidates.other', { percent: percent(result.other) })}</p>{/if}
      {/if}
      {#if shown.length}
        <ul class="result-grid">
          {#each shown as item, i (item.id)}
            <li><button type="button" class="result-tile" onclick={() => openCrop(item)} aria-label={t('similar.open', { label: label(item) })}>
              <span class="result-label">{#if isUnassigned(item)}{label(item)}{:else}<ScriptText text={item.label} />{/if}</span>
              {#if item.image && (item.origin !== 'corpus' || item.proxyable)}<Glyph {item} alt="" class="result-crop" eager={i < 12} />{:else}<span class="result-missing">{label(item)}</span>{/if}
              <span class="result-details">{#each sourceLines(item) as line}<span><ScriptLine {line} /></span>{/each}</span>
              <small class="result-score">{#if tileDate(item)}<span class="result-year">{tileDate(item)}</span>{/if}{item.score.toFixed(2)}</small>
            </button></li>
          {/each}
        </ul>
        <div class="result-legend"><ScriptLegend /></div>
      {:else}<p class="model-note">{t('imageSearch.results.none')}</p>{/if}
    </div>
  {/if}
</section>

<style>
  .image-search{border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:18px 20px 22px;margin:0 0 22px}
  .image-search.drag-over{border-color:var(--accent);background:var(--accent-hover)}
  .image-search-header{display:flex;align-items:center;justify-content:space-between;gap:12px}
  .image-search-header h2{font-size:15px;font-weight:600}
  .image-search-header .icon-button{color:var(--muted)}
  .model-panel{margin-top:10px;font-size:13px}
  .model-explain{max-width:62ch;line-height:1.6}
  .model-privacy{margin-top:8px;font-size:12px;color:var(--muted);line-height:1.5;max-width:62ch}
  .model-note{color:var(--muted);font-size:12px;margin-top:8px;line-height:1.5}
  .model-error{color:var(--wrong);font-size:12px;margin-top:8px}
  .model-actions{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:14px}
  .model-actions small{color:var(--muted);font-size:11px}
  .model-progress{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:12px;font-variant-numeric:tabular-nums}
  .model-progress progress{flex:1 1 220px;max-width:360px;accent-color:var(--accent-solid)}
  .model-status{display:flex;align-items:center;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted)}
  .model-status .update{font-size:12px;padding:6px 10px;color:var(--accent);border-color:var(--accent)}
  .image-drop{margin-top:14px;border:1.5px dashed var(--line-strong, var(--line));border-radius:10px;padding:26px 18px;text-align:center;display:grid;justify-items:center;gap:12px;font-size:13px}
  .image-work{display:grid;grid-template-columns:minmax(0,1fr) 190px;gap:18px;margin-top:14px}
  .image-stage{position:relative;height:380px;overflow:hidden;border-radius:10px;background:light-dark(#ebe8e3, #2a2a2f);touch-action:none;user-select:none}
  .image-stage:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
  .image-stage img{position:absolute;display:block;max-width:none;pointer-events:none}
  .image-keys{margin-top:6px;font-size:10px;color:var(--muted)}
  .image-side{display:flex;flex-direction:column;align-items:stretch;gap:10px}
  .image-side .quiet-link{text-align:left}
  .model-view{margin:0;display:grid;gap:4px;justify-items:start}
  .model-view canvas{width:96px;height:96px;border:1px solid var(--line);border-radius:6px;image-rendering:pixelated}
  .model-view figcaption{font-size:10px;color:var(--muted)}
  .image-results{margin-top:20px;border-top:1px solid var(--line);padding-top:14px}
  .result-tabs{display:flex;gap:6px}
  .result-tabs button{font-size:12px;padding:7px 11px;border-radius:5px;background:var(--surface-disabled);border-color:transparent}
  .result-tabs button[aria-pressed="true"]{color:var(--accent);background:var(--accent-light)}
  .candidate-chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}
  .candidate-chips button{display:flex;flex-direction:column;align-items:center;gap:2px;min-width:64px;padding:8px 12px;border-radius:8px}
  .candidate-chips button[aria-pressed="true"]{border-color:var(--accent);background:var(--accent-light)}
  .chip-chars{font-size:24px;line-height:1.2}
  .candidate-chips small{font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}
  .result-grid{list-style:none;margin:14px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:6px}
  .result-tile{position:relative;width:100%;height:150px;display:flex;align-items:center;justify-content:center;border:1px solid var(--line);border-radius:8px;padding:26px 14px 34px;background:var(--surface-tile);overflow:hidden}
  .result-tile:hover{background:var(--accent-tile)}
  .result-tile :global(.result-crop){width:100%;height:100%}
  .result-label{position:absolute;top:8px;left:10px;font-size:15px;line-height:1}
  .result-missing{font-size:34px;color:var(--muted)}
  .result-details{position:absolute;left:10px;right:44px;bottom:7px;display:flex;flex-direction:column;font-size:9px;line-height:1.3;color:var(--muted);text-align:left}
  .result-details span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .result-score{position:absolute;right:9px;bottom:8px;display:flex;flex-direction:column;align-items:end;font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}
  .result-year{color:var(--ink)}
  .result-legend{margin-top:10px}
  @media(max-width:760px){
    .image-search{padding:14px 14px 18px}
    .image-work{grid-template-columns:1fr}
    .image-stage{height:300px}
    .image-side{flex-direction:row;flex-wrap:wrap;align-items:center}
    .result-grid{grid-template-columns:repeat(auto-fill,minmax(96px,1fr))}
    .result-tile{height:128px}
  }
</style>
