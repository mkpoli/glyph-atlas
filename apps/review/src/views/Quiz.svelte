<script>
  import { onMount } from 'svelte'
  import Glyph from '../components/Glyph.svelte'
  import { catalogue, randomSeed, request, remember, stored, number } from '../lib/client.js'
  let { clientId, initialReading = '', inspect } = $props()
  let data = $state(null), items = $state([]), choices = $state({}), loaded = $state({}), failed = $state({})
  let reading = $state(''), loading = $state(true), saving = $state(false), error = $state('')
  let categoryOpen = $state(false), search = $state(''), completed = $state(0), last = $state(null)
  let roundId = crypto.randomUUID(), requestId = 0, closed = false
  const keys = 'qwertyasdfgh'.split('')
  const wrong = $derived(Object.values(choices).filter(v => v === 'wrong').length)
  const unsure = $derived(Object.values(choices).filter(v => v === 'unsure').length)
  const ready = $derived(items.length > 0 && items.every(i => loaded[i.id] || failed[i.id]) && items.some(i => loaded[i.id]))
  const categories = $derived((data?.categories ?? []).filter(c => c.pending > 0 && c.label.includes(search)))
  async function load(change = false) {
    const id = ++requestId; loading = true; error = ''; items = []; choices = {}; loaded = {}; failed = {}; roundId = crypto.randomUUID()
    try {
      const summary = await catalogue({ limit: 1, state: 'pending' })
      if (closed || id !== requestId) return
      data = summary
      const available = summary.categories.filter(c => c.pending > 0)
      if (change || !reading || !available.some(c => c.label === reading)) {
        const other = available.filter(c => c.label !== reading)
        const pool = (other.length ? other : available).slice(0, 35)
        reading = pool.length ? pool[randomSeed() % pool.length].label : ''
      }
      if (!reading) return
      const result = await catalogue({ reading, state: 'pending', limit: 12, seed: randomSeed() })
      if (!closed && id === requestId) items = result.items
    } catch (e) { if (!closed) error = e.message }
    finally { if (!closed && id === requestId) loading = false }
  }
  function choose(id, verdict = 'wrong') {
    if (saving || loading || !loaded[id]) return
    choices = { ...choices, [id]: choices[id] === verdict ? 'match' : verdict }
  }
  async function submit() {
    if (saving || !ready) return
    saving = true; error = ''
    const answers = items.filter(i => loaded[i.id]).map(i => ({ id: i.id, revision: i.revision, image_sha256: i.image_sha256, verdict: choices[i.id] || 'match' }))
    try {
      await request('/atlas/rounds', { id: roundId, client_id: clientId, label: reading, answers })
      last = { id: roundId, count: answers.length, label: reading }
      remember('atlas.last-round.' + clientId, last)
      completed += answers.length
      await load()
    } catch (e) { error = e.status === 409 ? 'This round changed. Your choices are kept. Reload the round to continue.' : e.message }
    finally { saving = false }
  }
  async function undo() {
    if (!last || saving) return
    saving = true; error = ''
    try {
      await request(`/atlas/rounds/${last.id}/undo`, { client_id: clientId })
      reading = last.label; completed = Math.max(0, completed - last.count); last = null
      remember('atlas.last-round.' + clientId, null); await load()
    } catch (e) { error = e.message }
    finally { saving = false }
  }
  function keydown(e) {
    if (document.querySelector('dialog[open]') || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return
    const index = keys.indexOf(e.key.toLowerCase())
    if (index >= 0 && items[index]) { e.preventDefault(); choose(items[index].id, e.shiftKey ? 'unsure' : 'wrong') }
    if (e.key === 'Enter' && (e.target.tagName !== 'BUTTON' || e.target.closest('.quiz-grid'))) { e.preventDefault(); submit() }
  }
  onMount(() => { reading = initialReading; last = stored('atlas.last-round.' + clientId, null); load(); return () => { closed = true } })
</script>

<svelte:window onkeydown={keydown} />
<section class="quiz-workspace">
  <div class="quiz-topline"><a href="#/" class="quiet-link">← Collection</a><div class="round-count"><span class="live-dot"></span>{number(completed)} reviewed this session</div>{#if last}<button class="undo-round" disabled={saving} onclick={undo}>↶ Undo last round</button>{/if}</div>
  <div class="quiz-heading"><div class="target-character" aria-label={`Target reading ${reading}`}>{reading || '字'}</div><div class="quiz-title"><p class="overline">QUICK REVIEW</p><h1>Spot the mismatch.</h1><p>Tap the ones that don’t read <strong>{reading || '…'}</strong>.</p></div><div class="round-switch"><button class="category-toggle" disabled={saving || loading} onclick={() => categoryOpen = !categoryOpen}>Change reading ⌄</button><button class="quiet-link" disabled={saving || loading} onclick={() => load(true)}>Skip round →</button></div></div>
  {#if categoryOpen}<div class="round-categories"><input aria-label="Find a category" placeholder="Find a reading…" bind:value={search}/><div class="category-options">{#each categories as c}<button onclick={() => { reading = c.label; categoryOpen = false; load() }}><span>{c.label}</span><small>{c.pending}</small></button>{/each}</div></div>{/if}
  {#if error}<div class="error-message" role="alert"><span>{error}</span><button disabled={saving} onclick={() => load()}>Reload round</button></div>{/if}
  <div class="quiz-grid" aria-label="Review round" aria-busy={loading}>
    {#if loading}{#each Array(12) as _}<div class="quiz-skeleton"></div>{/each}
    {:else}{#each items as item, i (item.id)}<div class="quiz-tile" class:wrong={choices[item.id] === 'wrong'} class:unsure={choices[item.id] === 'unsure'} class:unavailable={failed[item.id]}>
      <button class="quiz-choice" aria-label={`Character ${i + 1}: ${choices[item.id] || 'match'}`} aria-pressed={choices[item.id] === 'wrong'} disabled={saving || !loaded[item.id]} onclick={(e) => choose(item.id, e.shiftKey ? 'unsure' : 'wrong')}><Glyph {item} eager onload={id => loaded = { ...loaded, [id]: true }} onerror={id => failed = { ...failed, [id]: true }} /><span class="choice-mark">{choices[item.id] === 'wrong' ? '×' : choices[item.id] === 'unsure' ? '?' : ''}</span></button>
      <div class="quiz-tile-tools"><kbd>{keys[i]}</kbd><span class="choice-label">{failed[item.id] ? 'Image unavailable' : choices[item.id] === 'wrong' ? 'Doesn’t match' : choices[item.id] === 'unsure' ? 'Unsure' : ''}</span><button class:active={choices[item.id] === 'unsure'} aria-label={`Unsure about character ${i + 1}`} disabled={saving || !loaded[item.id]} onclick={() => choose(item.id, 'unsure')}>?</button><button aria-label={`Inspect character ${i + 1}`} disabled={saving} onclick={() => inspect(item.id, verdict => choices = { ...choices, [item.id]: verdict })}>↗</button></div>
    </div>{/each}{/if}
  </div>
  {#if !loading && !items.length}<div class="empty"><span class="empty-mark">✓</span><h2>All caught up.</h2><a href="#/flagged" class="primary">Review flagged characters →</a></div>
  {:else}<div class="quiz-actionbar"><div class="round-selection"><span class="selection-dot" class:has-flags={wrong > 0}></span><strong>{wrong} flagged</strong><span>{unsure} unsure</span><button class="bulk-toggle" disabled={saving || !ready} onclick={() => choices = Object.fromEntries(items.filter(i => loaded[i.id]).map(i => [i.id, wrong === items.filter(j => loaded[j.id]).length ? 'match' : 'wrong']))}>{wrong === items.filter(i => loaded[i.id]).length ? 'Clear' : 'Flag all'}</button>{#if Object.keys(failed).length}<small>{Object.keys(failed).length} unavailable · skipped</small>{/if}</div><div class="quiz-submit"><span class="keyboard-hint"><kbd>Enter</kbd> to continue</span><button class="primary" disabled={loading || saving || !ready} onclick={submit}>{saving ? 'Saving…' : wrong || unsure ? 'Save round' : 'All match'} <span>→</span></button></div></div>{/if}
</section>
