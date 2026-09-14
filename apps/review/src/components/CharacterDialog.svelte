<script>
  import { onMount, untrack } from 'svelte'
  import { character, request, suggestionsFor } from '../lib/client.js'
  import { decision, isSingle } from '../lib/issues.js'
  import Glyph from './Glyph.svelte'
  import IssuePicker from './IssuePicker.svelte'
  import ReadingSuggestions from './ReadingSuggestions.svelte'
  let { id, clientId, close, saved, onVerdict = null, previous = null, next = null, position = '' } = $props()
  let dialog, data = $state(null), error = $state(''), busy = $state(false)
  let reading = $state(''), note = $state(''), issue = $state(null), context = $state(false), correction = $state(null)
  let editingBox = $state(false), box = $state(null), start = null, contextElement = $state(null)
  let suggestions = $state(null), suggesting = $state(false), loaded = $state(false), imageFailed = $state(false)
  let closed = false, generation = 0, submission = null
  const boxStyle = $derived(data?.context_box && (box || data.box) ? (() => {
    const c = data.context_box, b = box || data.box
    return `left:${100 * (b.x - c.x) / c.w}%;top:${100 * (b.y - c.y) / c.h}%;width:${100 * b.w / c.w}%;height:${100 * b.h / c.h}%`
  })() : '')
  const suggestedIssue = $derived(suggestions?.candidates?.[0]?.engine === 'NDLkotenOCR' && suggestions.candidates[0].score >= .65 && !isSingle(suggestions.candidates[0].text) ? 'merged' : null)
  async function load(target) {
    const current = ++generation
    dialog?.scrollTo({ top: 0 })
    data = null; error = ''; issue = null; correction = null; note = ''; box = null; start = null; context = false; editingBox = false
    loaded = false; imageFailed = false; suggestions = null; suggesting = false; submission = null
    try {
      const result = await character(target)
      if (closed || current !== generation) return
      data = result; reading = result.label; suggesting = true
      suggestionsFor(result).then(value => { if (!closed && current === generation) { suggestions = value; suggesting = false } })
    } catch (e) { if (!closed && current === generation) error = e.message }
  }
  $effect(() => { const target = id; untrack(() => load(target)) })
  onMount(() => { dialog.showModal(); return () => { closed = true; generation++ } })
  function chooseIssue(value) { issue = value; correction = null; submission = null }
  async function save(matches = false) {
    if (busy || !data || !loaded || imageFailed) return
    const target = id, current = generation
    const value = matches || !issue ? { verdict: 'match' } : { ...decision(issue), correction }
    if (onVerdict) { onVerdict(value); close(); return }
    busy = true; error = ''
    const payload = { client_id: clientId, revision: data.revision, image_sha256: data.image_sha256,
      ...value, issue: matches ? 'reading' : issue || 'reading', note,
      ...(reading !== data.label ? { reading } : {}), ...(box ? { box } : {}) }
    const signature = JSON.stringify(payload)
    if (!submission || submission.signature !== signature) submission = { signature, id: crypto.randomUUID() }
    try {
      const result = await request('/atlas/characters/' + encodeURIComponent(target), { id: submission.id, ...payload })
      if (!closed && current === generation) saved(target, result)
    } catch (e) { if (!closed && current === generation) error = e.message }
    finally { busy = false }
  }
  function point(e) {
    const r = contextElement.getBoundingClientRect(), c = data.context_box
    return { x: Math.round(c.x + Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * c.w),
      y: Math.round(c.y + Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) * c.h) }
  }
  function down(e) { if (!editingBox || !data.context_box || busy) return; e.preventDefault(); start = point(e); contextElement.setPointerCapture(e.pointerId) }
  function move(e) {
    if (!start) return
    const end = point(e), c = data.context_box
    const x = Math.min(c.x + c.w - 1, Math.min(start.x, end.x)), y = Math.min(c.y + c.h - 1, Math.min(start.y, end.y))
    box = { x, y, w: Math.min(c.x + c.w - x, Math.max(1, Math.abs(end.x - start.x))), h: Math.min(c.y + c.h - y, Math.max(1, Math.abs(end.y - start.y))) }
  }
</script>

<dialog class="character-dialog" bind:this={dialog} oncancel={close} onclick={e => { if (e.target === dialog) close() }} aria-label="Character reviewer">
  <div class="inspector">
    <header class="inspector-header"><span class="overline">CHARACTER</span><div class="inspector-navigation"><span>{position}</span><button class="icon-button previous-character" aria-label="Previous character" disabled={busy || !previous} onclick={() => previous?.()}>←</button><button class="icon-button next-character" aria-label="Next character" disabled={busy || !next} onclick={() => next?.()}>→</button><button class="icon-button close-inspector" aria-label="Close reviewer" onclick={close}>×</button></div></header>
    {#if error}<div class="error-message" role="alert">{error}<button disabled={busy} onclick={() => load(id)}>Reload character</button></div>{/if}
    {#if data}
      <div class="inspector-title"><h2>{data.label}</h2><span class="state-pill" class:flagged={data.state === 'flagged'}>{data.state === 'checked' ? 'Checked' : data.state === 'flagged' ? 'Flagged' : 'Unreviewed'}</span></div>
      <div class="inspector-tabs"><button class:active={!context} onclick={() => context = false}>Crop</button><button class:active={context} onclick={() => context = true}>Context</button></div>
      {#if context}<div class="context-region" class:drawing={editingBox} bind:this={contextElement} onpointerdown={down} onpointermove={move} onpointerup={() => start = null} onpointercancel={() => start = null} role="img" aria-label="Surrounding strokes"><img src={data.context_image} alt="Strokes around this character" />{#if boxStyle}<span class="context-outline" style={boxStyle}></span>{/if}</div><div class="context-caption"><span>{data.source}{data.page_number ? ` · ${data.page_number}` : ''}</span>{#if data.context_box && !onVerdict}<button class:active={editingBox} disabled={busy} onclick={() => editingBox = !editingBox}>{editingBox ? 'Drag a new crop' : 'Adjust crop'}</button>{/if}</div>
      {:else}<div class="inspector-crop">{#key data.image}<Glyph item={data} eager onload={() => loaded = true} onerror={() => imageFailed = true} />{/key}</div>{/if}
      <div class="inspector-question"><strong>What’s wrong?</strong><span>Choose one</span></div>
      <IssuePicker value={issue} choose={chooseIssue} suggested={suggestedIssue} disabled={busy} />
      <ReadingSuggestions result={suggestions} loading={suggesting} {issue} reading={data.label} value={correction} disabled={busy} choose={value => correction = value} />
      {#if !onVerdict}<details class="advanced-edit"><summary>Adjust crop or reading</summary><label class="reading-input">Reading<input aria-label="Character reading" bind:value={reading} maxlength="32" disabled={busy} /></label><button type="button" class="quiet-link adjust-crop" onclick={() => { context = true; editingBox = true }}>Adjust crop ↗</button><textarea aria-label="Review note" bind:value={note} rows="2" maxlength="2000" placeholder="Optional note" disabled={busy}></textarea>{#if box}<div class="crop-change">Crop adjusted<button type="button" onclick={() => box = null}>Reset</button></div>{/if}</details>{/if}
    {:else if !error}<div class="inspector-skeleton"></div>{/if}
  </div>
  <footer class="inspector-savebar">
    {#if imageFailed}<span role="alert">Image unavailable</span>{/if}
    <button class="primary save-character" disabled={busy || !data || !loaded || imageFailed} onclick={() => save()}>{busy ? 'Saving…' : issue ? (onVerdict ? 'Use this error' : next ? 'Save issue & next' : 'Save issue') : (onVerdict ? 'Matches' : next ? 'Looks right & next' : 'Looks right')} <span>{issue ? '→' : '✓'}</span></button>
    {#if issue}<button class="quiet-link looks-right" disabled={busy || !loaded || imageFailed} onclick={() => { issue = null; correction = null; save(true) }}>It looks right</button>{/if}
  </footer>
</dialog>
