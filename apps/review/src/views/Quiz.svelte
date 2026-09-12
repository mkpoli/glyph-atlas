<script>
  import { onMount } from 'svelte'
  import Glyph from '../components/Glyph.svelte'
  import IssuePicker from '../components/IssuePicker.svelte'
  import ReadingSuggestions from '../components/ReadingSuggestions.svelte'
  import { catalogue, randomSeed, request, remember, stored, number, suggestionsFor } from '../lib/client.js'
  import { issues, issueTitle, decision } from '../lib/issues.js'
  let { clientId, initialReading = '', inspect } = $props()
  let data = $state(null), items = $state([]), choices = $state({}), selected = $state({})
  let loaded = $state({}), failed = $state({}), suggestions = $state({})
  let reading = $state(''), loading = $state(true), saving = $state(false), error = $state('')
  let categoryOpen = $state(false), search = $state(''), completed = $state(0), last = $state(null)
  let roundId = crypto.randomUUID(), requestId = 0, closed = false
  const keys = 'qwertyasdfgh'.split('')
  const selection = $derived(Object.keys(selected).filter(id => selected[id]))
  const flagged = $derived(Object.keys(choices).filter(id => choices[id].verdict !== 'match').length)
  const ready = $derived(items.length > 0 && items.every(i => loaded[i.id] || failed[i.id]) && items.some(i => loaded[i.id]))
  const categories = $derived((data?.categories ?? []).filter(c => c.pending > 0 && c.label.includes(search)))

  async function load(change = false) {
    const id = ++requestId
    loading = true; error = ''; items = []; choices = {}; selected = {}; loaded = {}; failed = {}; suggestions = {}; roundId = crypto.randomUUID()
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
    } catch (e) { if (!closed && id === requestId) error = e.message }
    finally { if (!closed && id === requestId) loading = false }
  }
  function select(id) {
    if (saving || loading || !loaded[id]) return
    selected = { ...selected, [id]: !selected[id] }
  }
  async function suggest(item) {
    const round = roundId
    const result = await suggestionsFor(item)
    if (!closed && roundId === round) suggestions = { ...suggestions, [item.id]: result }
  }
  function assign(issue) {
    if (saving || !selection.length) return
    const updates = { ...choices }
    for (const id of selection) {
      updates[id] = decision(issue)
      if (['reading', 'merged'].includes(issue)) suggest(items.find(i => i.id === id))
    }
    choices = updates; selected = {}; error = ''
  }
  function inspectChoice(item) {
    inspect(item.id, value => {
      choices = { ...choices, [item.id]: value }
      selected = { ...selected, [item.id]: false }
      if (['reading', 'merged'].includes(value.issue)) suggest(item)
    })
  }
  async function submit() {
    if (saving || !ready) return
    if (selection.length) { error = 'Choose an error type for the selected crops.'; return }
    saving = true; error = ''
    const answers = items.filter(i => loaded[i.id]).map(i => ({ id: i.id, revision: i.revision, image_sha256: i.image_sha256,
      ...(choices[i.id] || { verdict: 'match' }) }))
    try {
      await request('/atlas/rounds', { id: roundId, client_id: clientId, label: reading, answers })
      last = { id: roundId, count: answers.length, label: reading }
      remember('atlas.last-round.' + clientId, last); completed += answers.length
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
    if (index >= 0 && items[index]) { e.preventDefault(); select(items[index].id) }
    if (/^[1-5]$/.test(e.key)) { e.preventDefault(); assign(issues[Number(e.key) - 1].id) }
    if (e.key === 'Escape') selected = {}
    if (e.key === 'Enter' && (e.target.tagName !== 'BUTTON' || e.target.closest('.quiz-grid'))) { e.preventDefault(); submit() }
  }
  onMount(() => { reading = initialReading; last = stored('atlas.last-round.' + clientId, null); load(); return () => { closed = true } })
</script>

<svelte:window onkeydown={keydown} />
<section class="quiz-workspace">
  <div class="quiz-topline"><a href="#/" class="quiet-link">← Collection</a><div class="round-count"><span class="live-dot"></span>{number(completed)} reviewed this session</div>{#if last}<button class="undo-round" disabled={saving} onclick={undo}>↶ Undo last round</button>{/if}</div>
  <div class="quiz-heading"><div class="target-character" aria-label={`Target reading ${reading}`}>{reading || '字'}</div><div class="quiz-title"><p class="overline">QUICK REVIEW</p><h1>Which aren’t {reading || '…'}?</h1><p>Select crops. Choose an error. Save.</p></div><div class="round-switch"><button class="category-toggle" disabled={saving || loading} onclick={() => categoryOpen = !categoryOpen}>Change reading ⌄</button><button class="quiet-link" disabled={saving || loading} onclick={() => load(true)}>Skip round →</button></div></div>
  {#if categoryOpen}<div class="round-categories"><input aria-label="Find a category" placeholder="Find a reading…" bind:value={search}/><div class="category-options">{#each categories as c}<button disabled={saving} onclick={() => { reading = c.label; categoryOpen = false; load() }}><span>{c.label}</span><small>{c.pending}</small></button>{/each}</div></div>{/if}
  <div class="round-error-tools" class:has-selection={selection.length > 0}>
  <div class="selection-toolbar"><strong>{selection.length ? `${selection.length} selected` : 'Select the mismatches'}</strong><button class="bulk-toggle" disabled={saving || !ready} onclick={() => selected = selection.length === items.filter(i => loaded[i.id]).length ? {} : Object.fromEntries(items.filter(i => loaded[i.id]).map(i => [i.id, true]))}>{selection.length === items.filter(i => loaded[i.id]).length && selection.length ? 'Deselect all' : 'Select all'}</button><span class="keyboard-hint">1–5 error type</span></div>
  <IssuePicker choose={assign} disabled={saving || !selection.length} compact />
  </div>
  {#if error}<div class="error-message" role="alert"><span>{error}</span>{#if error.includes('changed')}<button disabled={saving} onclick={() => load()}>Reload round</button>{/if}</div>{/if}
  <div class="quiz-grid" aria-label="Review round" aria-busy={loading}>
    {#if loading}{#each Array(12) as _}<div class="quiz-skeleton"></div>{/each}
    {:else}{#each items as item, i (item.id)}
      <div class="quiz-tile" class:selected={selected[item.id]} class:wrong={choices[item.id]?.verdict === 'wrong'} class:unsure={choices[item.id]?.verdict === 'unsure'} class:unavailable={failed[item.id]}>
        <button class="quiz-choice" aria-label={`Select character ${i + 1}`} aria-pressed={!!selected[item.id]} disabled={saving || !loaded[item.id]} onclick={() => select(item.id)}><Glyph {item} eager onload={id => loaded = { ...loaded, [id]: true }} onerror={id => failed = { ...failed, [id]: true }} /><span class="choice-mark">{selected[item.id] ? '✓' : choices[item.id]?.verdict === 'wrong' ? '×' : choices[item.id]?.verdict === 'unsure' ? '?' : ''}</span></button>
        <div class="quiz-tile-tools"><kbd>{keys[i]}</kbd><span class="choice-label">{failed[item.id] ? 'Unavailable' : choices[item.id]?.issue ? issueTitle(choices[item.id].issue) : ''}</span>{#if choices[item.id]}<button class="clear-choice" aria-label={`Clear error for character ${i + 1}`} disabled={saving} onclick={() => { const next = { ...choices }; delete next[item.id]; choices = next }}>×</button>{/if}<button class="inspect-choice" aria-label={`Inspect character ${i + 1}`} disabled={saving} onclick={() => inspectChoice(item)}>↗</button></div>
        {#if ['reading','merged'].includes(choices[item.id]?.issue)}<ReadingSuggestions result={suggestions[item.id]} loading={!suggestions[item.id]} issue={choices[item.id].issue} reading={item.label} value={choices[item.id].correction} disabled={saving} choose={value => choices = { ...choices, [item.id]: { ...choices[item.id], correction: value } }} />{/if}
      </div>
    {/each}{/if}
  </div>
  {#if !loading && !items.length}<div class="empty"><span class="empty-mark">✓</span><h2>All caught up.</h2><a href="#/flagged" class="primary">Review flagged characters →</a></div>
  {:else}<div class="quiz-actionbar"><div class="round-selection"><span class="selection-dot" class:has-flags={flagged > 0}></span><strong>{flagged} issues</strong><span>{items.filter(i => loaded[i.id]).length - flagged - selection.filter(id => !choices[id]).length} match</span>{#if Object.keys(failed).length}<small>{Object.keys(failed).length} unavailable · skipped</small>{/if}</div><div class="quiz-submit"><span class="keyboard-hint"><kbd>Enter</kbd> to save</span><button class="primary" disabled={loading || saving || !ready || selection.length > 0} onclick={submit}>{saving ? 'Saving…' : selection.length ? 'Choose error type' : flagged ? 'Save round' : `All ${reading}`} <span>→</span></button></div></div>{/if}
</section>
