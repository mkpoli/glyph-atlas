<script>
  import { onMount } from 'svelte'
  import { character, request } from '../lib/client.js'
  import Glyph from './Glyph.svelte'
  let { id, clientId, close, saved, onVerdict = null } = $props()
  let dialog, data = $state(null), error = $state(''), busy = $state(false)
  let reading = $state(''), note = $state(''), issue = $state('reading'), context = $state(false)
  let editingBox = $state(false), box = $state(null), start = null, contextElement = $state(null)
  let closed = false
  const boxStyle = $derived(data?.context_box && (box || data.box) ? (() => {
    const c = data.context_box, b = box || data.box
    return `left:${100 * (b.x - c.x) / c.w}%;top:${100 * (b.y - c.y) / c.h}%;width:${100 * b.w / c.w}%;height:${100 * b.h / c.h}%`
  })() : '')
  async function load() {
    error = ''
    try { const result = await character(id); if (!closed) { data = result; reading = data.label; box = null } }
    catch (e) { if (!closed) error = e.message }
  }
  onMount(() => { dialog.showModal(); load(); return () => { closed = true } })
  async function verdict(value) {
    if (onVerdict) { onVerdict(value); close(); return }
    busy = true; error = ''
    try {
      await request('/atlas/characters/' + encodeURIComponent(id), { id: crypto.randomUUID(), client_id: clientId,
        revision: data.revision, image_sha256: data.image_sha256, reading, verdict: value, issue, note, ...(box ? { box } : {}) })
      saved(); if (!closed) close()
    } catch (e) { error = e.message }
    finally { busy = false }
  }
  function point(e) {
    const r = contextElement.getBoundingClientRect(), c = data.context_box
    return { x: Math.round(c.x + Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * c.w),
      y: Math.round(c.y + Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) * c.h) }
  }
  function down(e) { if (!editingBox || !data.context_box) return; e.preventDefault(); start = point(e); contextElement.setPointerCapture(e.pointerId) }
  function move(e) { if (!start) return; const end = point(e); box = { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), w: Math.max(2, Math.abs(end.x - start.x)), h: Math.max(2, Math.abs(end.y - start.y)) } }
</script>

<dialog class="character-dialog" bind:this={dialog} oncancel={close} onclick={e => { if (e.target === dialog) close() }} onkeydown={e => { if (e.key === 'Escape') close() }} aria-label="Character inspector">
  <div class="inspector">
    <header class="inspector-header"><span class="overline">CHARACTER</span><button class="icon-button close-inspector" aria-label="Close inspector" onclick={close}>×</button></header>
    {#if error}<div class="error-message" role="alert">{error}<button onclick={load}>Reload</button></div>{/if}
    {#if data}
      <div class="inspector-title"><h2>{data.label}</h2><span class="state-pill" class:flagged={data.state === 'flagged'}>{data.state === 'checked' ? 'Checked' : data.state === 'flagged' ? 'Flagged' : 'Unreviewed'}</span></div>
      <div class="inspector-tabs"><button class:active={!context} onclick={() => context = false}>Crop</button><button class:active={context} onclick={() => context = true}>Context</button></div>
      {#if context}<div class="context-region" class:drawing={editingBox} bind:this={contextElement} onpointerdown={down} onpointermove={move} onpointerup={() => start = null} onpointercancel={() => start = null} role="img" aria-label="Surrounding strokes"><img src={data.context_image} alt="Strokes around this character" />{#if boxStyle}<span class="context-outline" style={boxStyle}></span>{/if}</div><div class="context-caption"><span>{data.source}{data.page_number ? ` · ${data.page_number}` : ''}</span>{#if data.context_box && !onVerdict}<button class:active={editingBox} onclick={() => editingBox = !editingBox}>{editingBox ? 'Drag a new crop' : 'Adjust crop'}</button>{/if}</div>
      {:else}<div class="inspector-crop"><Glyph item={data} eager /></div>{/if}
      {#if data.jibo}<div class="character-detail"><span>字母</span><strong>{data.jibo}</strong></div>{/if}
      {#if onVerdict}<div class="inspector-decisions"><button class="primary" onclick={() => verdict('match')}>Matches</button><button class="negative" onclick={() => verdict('wrong')}>Doesn’t match</button><button onclick={() => verdict('unsure')}>Unsure</button></div>
      {:else}<form onsubmit={e => { e.preventDefault(); verdict('match') }}>
        <label class="reading-input">Reading<input aria-label="Character reading" bind:value={reading} required maxlength="32" /></label>
        <div class="issue-options" aria-label="Issue">{#each [['reading','Reading'],['crop','Crop'],['merged','Joined'],['blank','Blank'],['other','Other']] as [value,text]}<button type="button" class:active={issue === value} onclick={() => { issue = value; if (value === 'crop') context = true }}>{text}</button>{/each}</div>
        <details class="inspector-note"><summary>Add a note</summary><textarea aria-label="Review note" bind:value={note} rows="2" maxlength="2000" placeholder="Optional"></textarea></details>
        {#if box}<div class="crop-change">Crop adjusted<button type="button" onclick={() => box = null}>Reset</button></div>{/if}
        <button class="primary save-character" disabled={busy || !reading.trim()}>{busy ? 'Saving…' : 'Save & confirm'} <span>✓</span></button>
        <div class="inspector-secondary"><button type="button" disabled={busy} onclick={() => verdict('wrong')}>Flag issue</button><button type="button" disabled={busy} onclick={() => verdict('unsure')}>Unsure</button></div>
      </form>{/if}
    {:else if !error}<div class="inspector-skeleton"></div>{/if}
  </div>
</dialog>
