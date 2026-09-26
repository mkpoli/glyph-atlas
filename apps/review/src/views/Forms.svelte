<script>
  import { onMount, untrack } from 'svelte'
  import { replaceState } from '$app/navigation'
  import { page } from '$app/state'
  import ReferenceGlyph from '../components/ReferenceGlyph.svelte'
  import FormReview from '../components/FormReview.svelte'
  import { settle } from '../lib/settle.js'
  import { families as loadFamilies, family as loadFamily, members as loadMembers, decide, split as loadSplit } from '../lib/forms.js'
  import { number, reviewer, stored, remember } from '../lib/client.js'
  import { t, around, localize } from '../lib/i18n.svelte.js'

  // `initial` is what the server rendered: the family list and the family on show, arranged by shape.
  let { initialFamily = '', initial = null } = $props()
  const first = untrack(() => initial)
  let list = $state(first?.list ?? []), filter = $state(''), current = $state(null), code = $state(first?.family?.code_point ?? '')
  let active = $state(0), open = $state(null), glyphs = $state([]), total = $state(0), order = $state('typical')
  let chosen = $state(new Set()), anchor = null, busy = $state(false), error = $state(''), notice = $state('')
  let correcting = $state(false), correction = $state('')
  // Clusters picked together with ctrl/cmd-click, shift-click or X; one form then names them all.
  let picked = $state(new Set()), pickAnchor = null, arrange = $state('shape')
  // A cluster divided by shape on request; groups are a way to select glyphs, not a stored result.
  let splitK = $state(0), groups = $state([])
  // Clusters with glyphs still to name come first; finished ones keep their order below them.
  let openFirst = $state(stored('atlas.forms.unassignedFirst', true) !== false)
  // What is on its way: a family being switched to, an opened cluster's glyphs, a split.
  let loadingFamily = $state(''), loadingMembers = $state(false), loadingSplit = $state(false)
  // Cluster by cluster review of every glyph, entered from the cluster grid.
  let reviewing = $state(false)
  // A cluster is done once it is named, or every glyph has a form or was reported as not belonging.
  const isOpen = c => !c.form && c.assigned + c.rejected < c.count
  const cluster = $derived(current?.items[active] ?? null)
  const shown = $derived(list.filter(f => !filter.trim() || f.char.includes(filter.trim()) || f.label.includes(filter.trim())
    || f.code_point.toLowerCase().includes(filter.trim().toLowerCase())))
  const byForm = $derived(new Map((current?.forms ?? []).map(f => [f.char, f])))
  const pickedClusters = $derived((current?.items ?? []).filter(c => picked.has(c.id)))
  const target = $derived(chosen.size ? t('forms.target.selectedGlyphs', { count: chosen.size })
    : pickedClusters.length > 1 ? t('forms.target.clusters', { clusters: pickedClusters.length, glyphs: number(pickedClusters.reduce((n, c) => n + c.count, 0)) })
    : pickedClusters.length === 1 ? t('forms.target.clusterGlyphs', { label: pickedClusters[0].label, count: pickedClusters[0].count })
    : cluster ? t('forms.target.clusterGlyphs', { label: cluster.label, count: cluster.count }) : '')

  async function refreshList() { list = (await loadFamilies()).items }
  /** A family as the grid shows it: with open clusters first when the reader asks for that. */
  function arranged(loaded) {
    if (openFirst) loaded.items = [...loaded.items.filter(isOpen), ...loaded.items.filter(c => !isOpen(c))]
    return loaded
  }
  if (first?.family) { const shown = arranged(first.family); current = shown; active = Math.max(0, shown.items.findIndex(isOpen)) }
  async function pick(codePoint, keep = false) {
    error = ''
    code = codePoint
    const address = localize('/forms/' + codePoint)
    if (page.url.pathname !== address) replaceState(address, page.state)
    const keepId = keep ? current?.items[active]?.id : null
    if (!keep) loadingFamily = codePoint
    let loaded
    try { loaded = await loadFamily(codePoint, arrange) } finally { if (loadingFamily === codePoint) loadingFamily = '' }
    // A family picked while this one loaded replaces it.
    if (code !== codePoint) return
    current = arranged(loaded)
    if (keepId) active = Math.max(0, current.items.findIndex(c => c.id === keepId))
    if (!keep) { picked = new Set(); pickAnchor = null; reviewing = false; active = Math.max(0, current.items.findIndex(isOpen)); close() }
  }
  async function show(index) {
    active = index; open = current.items[index].id; chosen = new Set(); anchor = null
    // An opened cluster is the only target; clusters picked on the grid are let go.
    picked = new Set(); pickAnchor = null
    await members(0)
  }
  // An opened cluster's first glyphs, with its tiles shown as placeholders until they come.
  async function members(offset) {
    const id = open
    glyphs = []; loadingMembers = true
    try {
      const page = await loadMembers(id, offset, 240, order)
      if (open === id) { glyphs = page.items; total = page.total }
    } finally { if (open === id) loadingMembers = false }
  }
  async function reorder(value) { order = value; chosen = new Set(); anchor = null; await members(0) }
  async function more() {
    const page = await loadMembers(open, glyphs.length, 240, order)
    glyphs = [...glyphs, ...page.items]
  }
  function close() { open = null; glyphs = []; loadingMembers = false; chosen = new Set(); anchor = null; splitK = 0; groups = [] }
  async function divide(k) {
    splitK = k; chosen = new Set(); anchor = null; groups = []; loadingSplit = Boolean(k)
    const cluster = open
    let result
    try { result = k ? (await loadSplit(cluster, k)).groups : [] } finally { if (splitK === k && open === cluster) loadingSplit = false }
    // Only the answer for the split still asked for is shown.
    if (splitK === k && open === cluster) groups = result
  }
  function selectGroup(group) {
    const next = new Set(chosen)
    const all = group.ids.every(id => next.has(id))
    for (const id of group.ids) all ? next.delete(id) : next.add(id)
    chosen = next
  }
  function toggleId(id) {
    const next = new Set(chosen)
    next.has(id) ? next.delete(id) : next.add(id)
    chosen = next
  }
  function toggle(index, event) {
    const next = new Set(chosen)
    if (event.shiftKey && anchor != null) {
      const [from, to] = [Math.min(anchor, index), Math.max(anchor, index)]
      for (let i = from; i <= to; i++) next.add(glyphs[i].id)
    } else if (next.has(glyphs[index].id)) next.delete(glyphs[index].id)
    else next.add(glyphs[index].id)
    anchor = index; chosen = next
  }
  async function apply(form, kind = null) {
    if (busy || (!cluster && !chosen.size)) return
    busy = true; error = ''
    try {
      const units = [...chosen]
      const targets = units.length ? [] : pickedClusters.length ? pickedClusters.map(c => c.id) : [cluster.id]
      let result = { count: 0 }
      // A decision covers at most 1,000 glyphs; a larger selection is sent in parts.
      for (let i = 0; i < units.length; i += 1000)
        result = { count: result.count + (await decide({ kind: kind ?? 'glyph', units: units.slice(i, i + 1000), client_id: reviewer(), ...(kind === 'inherit' ? {} : { form }) })).count }
      // One decision per cluster, so each keeps its own record and can be withdrawn on its own.
      for (const id of targets) result = { count: result.count + (await decide({ kind: 'cluster', cluster: id, form, client_id: reviewer() })).count }
      picked = new Set(); pickAnchor = null
      notice = form ? t('forms.notice.assigned', { form, count: result.count })
        : kind === 'inherit' ? t('forms.notice.inherited', { count: result.count }) : t('forms.notice.cleared', { count: result.count })
      setTimeout(() => notice = '', 2200)
      const wasOpen = open, index = active
      await pick(code, true)
      await refreshList()
      if (wasOpen) { const page = await loadMembers(wasOpen, 0, Math.min(500, Math.max(240, glyphs.length)), order); glyphs = page.items; chosen = new Set(); if (splitK) groups = (await loadSplit(wasOpen, splitK)).groups }
      // With open clusters first, the one just named moved below, and the next took its place.
      else if (!units.length) active = nextOpen(openFirst ? Math.max(-1, index - targets.length) : index)
    } catch (e) { error = e.message } finally { busy = false }
  }
  // A bad crop or a glyph of another character is not a form: it is reported, and leaves the family.
  async function flag(issue) {
    if (busy || !chosen.size) return
    busy = true; error = ''
    try {
      const units = [...chosen], result = { count: 0 }
      for (let i = 0; i < units.length; i += 1000)
        result.count += (await decide({ kind: 'glyph', units: units.slice(i, i + 1000), issue, client_id: reviewer(),
          ...(issue === 'character' && correction.trim() ? { character: correction.trim() } : {}) })).count
      notice = t('forms.notice.reported', { count: result.count, issue: issue === 'crop' ? t('forms.reportIssue.crop') : t('forms.reportIssue.character') })
      setTimeout(() => notice = '', 2200)
      correcting = false; correction = ''
      await pick(code, true)
      await refreshList()
      const page = await loadMembers(open, 0, Math.min(500, Math.max(240, glyphs.length)), order); glyphs = page.items; chosen = new Set()
      if (splitK) groups = (await loadSplit(open, splitK)).groups
    } catch (e) { error = e.message } finally { busy = false }
  }
  function nextOpen(from) {
    const after = current.items.findIndex((c, i) => i > from && isOpen(c))
    return after >= 0 ? after : Math.min(from + 1, current.items.length - 1)
  }
  async function reviewed({ reported, issue, form, count }) {
    notice = [reported ? t('forms.notice.reported', { count: reported, issue: issue === 'crop' ? t('forms.reportIssue.crop') : t('forms.reportIssue.character') }) : '',
      form ? t('forms.notice.assigned', { form, count }) : ''].filter(Boolean).join(' · ')
    if (notice) setTimeout(() => notice = '', 2200)
    await pick(code, true)
    await refreshList()
  }
  function leaveReview(index) { reviewing = false; active = Math.min(index, current.items.length - 1); scrollActive() }
  function keydown(event) {
    if (reviewing) return
    if (!current || event.target.closest?.('input, textarea') || event.metaKey || event.ctrlKey || event.altKey) return
    const keys = '1234567890'
    if (keys.includes(event.key) && current.forms[keys.indexOf(event.key)]) { event.preventDefault(); apply(current.forms[keys.indexOf(event.key)].char) }
    else if (!open && (event.key === 'j' || event.key === 'ArrowDown')) { event.preventDefault(); active = Math.min(active + 1, current.items.length - 1); scrollActive() }
    else if (!open && (event.key === 'k' || event.key === 'ArrowUp')) { event.preventDefault(); active = Math.max(active - 1, 0); scrollActive() }
    else if (event.key === 'Enter' && !open) { event.preventDefault(); show(active) }
    else if (!open && (event.key === 'r' || event.key === 'R')) { event.preventDefault(); reviewing = true }
    else if (!open && (event.key === 'x' || event.key === 'X')) { event.preventDefault(); togglePick() }
    else if (event.key === 'Escape' && !open && picked.size) { event.preventDefault(); picked = new Set() }
    else if (event.key === 'Escape' && open) { event.preventDefault(); close() }
    // Clearing gives selected glyphs back to their cluster, or a cluster its unnamed state.
    else if (event.key === 'Backspace' && chosen.size) { event.preventDefault(); apply(null, 'inherit') }
    else if (event.key === 'Backspace' && cluster?.form) { event.preventDefault(); apply(null) }
  }
  function choose(index, event) {
    const id = current.items[index].id
    const from0 = pickAnchor == null ? -1 : current.items.findIndex(c => c.id === pickAnchor)
    if (event.shiftKey && from0 >= 0) {
      const next = new Set(picked)
      const [from, to] = [Math.min(from0, index), Math.max(from0, index)]
      for (let i = from; i <= to; i++) next.add(current.items[i].id)
      picked = next
    } else if (event.ctrlKey || event.metaKey) {
      const next = new Set(picked.size ? picked : [current.items[active].id])
      next.has(id) ? next.delete(id) : next.add(id)
      picked = next; pickAnchor = id
    } else { picked = new Set(); pickAnchor = id }
    active = index
  }
  function togglePick() {
    const id = current.items[active].id, next = new Set(picked)
    next.has(id) ? next.delete(id) : next.add(id)
    picked = next; pickAnchor = id
  }
  async function toggleOpenFirst() { openFirst = !openFirst; remember('atlas.forms.unassignedFirst', openFirst); await pick(code, true) }
  async function rearrange(value) { arrange = value; const id = cluster?.id; await pick(code, true); active = Math.max(0, current.items.findIndex(c => c.id === id)) }
  function scrollActive() { requestAnimationFrame(() => document.querySelector('.form-cluster.active')?.scrollIntoView({ block: 'nearest' })) }
  onMount(async () => {
    // The server arranged the family with open clusters first; a reader who turned that off gets theirs.
    if (first?.family) { if (!openFirst) await pick(code, true); return }
    try {
      await refreshList()
      await pick(initialFamily || list[0]?.code_point)
    } catch (e) { error = e.message }
  })
</script>

<svelte:window onkeydown={keydown} />

<section class="forms">
  <header class="forms-heading">
    <p class="overline">{t('forms.overline')}</p>
    <h1>{t('forms.heading')}</h1>
    <p class="forms-lede">{t('forms.lede')}</p>
  </header>
  {#if error}<p class="error-message">{error}</p>{/if}
  <div class="forms-layout">
    <aside class="family-list" aria-label={t('forms.families.label')}>
      <input type="search" placeholder={t('forms.findFamily.placeholder')} bind:value={filter} aria-label={t('forms.findFamily.aria')} />
      <ol aria-busy={!list.length && !error}>
        {#if !list.length && !error}{#each Array(12) as _, i (i)}<li class="family-skeleton" aria-hidden="true"><span class="shimmer"></span><span class="shimmer"></span></li>{/each}{/if}
        {#each shown as f (f.code_point)}
          <li><button class:current={f.code_point === code} onclick={() => pick(f.code_point)}>
            <span class="family-char">{f.char}</span>
            <span class="family-meta"><span>{number(f.count)}</span><small>{t('forms.clusters.count', { count: f.clusters })}</small></span>
            <span class="family-progress" aria-label={t('forms.percentDone', { percent: Math.round(100 * (f.assigned + f.rejected) / f.count) })}><i style={`width:${100 * f.assigned / f.count}%`}></i><i class="rejected" style={`width:${100 * f.rejected / f.count}%`}></i></span>
          </button></li>
        {/each}
      </ol>
    </aside>

    {#if loadingFamily || (!current && !error)}
      <div class="family-panel" aria-busy="true">
        <div class="family-title"><span class="shimmer title-skeleton" aria-hidden="true"></span><span class="shimmer line-skeleton" aria-hidden="true"></span></div>
        <div class="palette-skeleton" aria-hidden="true">{#each Array(6) as _, i (i)}<span class="shimmer"></span>{/each}</div>
        <ol class="cluster-grid" aria-hidden="true">
          {#each Array(6) as _, i (i)}<li class="form-cluster cluster-skeleton"><span class="shimmer line-skeleton"></span><span class="cluster-samples">{#each Array(12) as _, j (j)}<span class="shimmer"></span>{/each}</span></li>{/each}
        </ol>
      </div>
    {:else if current}
      <div class="family-panel">
        <div class="family-title">
          <h2>{current.char}</h2>
          <p><strong>{number(current.count)}</strong> {t('forms.glyphsLabel')} · {current.clusters} {t('forms.clustersLabel')} · <strong>{number(current.assigned)}</strong> {t('forms.assignedLabel')}{#if current.rejected}{' · '}<strong>{number(current.rejected)}</strong> {t('forms.rejectedLabel')}{/if}</p>
        </div>

        {#if reviewing}
          <FormReview family={current} start={active} {isOpen} onsaved={reviewed} onexit={leaveReview} />
        {:else}

        <div class="form-palette" aria-label={t('forms.palette.label')}>
          <p class="palette-target">{#if target}{t('forms.appliesTo')} <strong>{target}</strong>{:else}{t('forms.chooseCluster')}{/if}</p>
          <div class="palette-forms">
            {#each current.forms as form, i (form.char)}
              <button class="form-choice" disabled={busy || !target} onclick={() => apply(form.char)} title={form.name ?? form.code_point}>
                <ReferenceGlyph char={form.char} code_point={form.code_point} size="lg" script={form.script} />
                <span class="form-source">{form.jibo ?? ''}</span>
                <small>{form.code_point}</small>
                {#if i < 10}<kbd>{'1234567890'[i]}</kbd>{/if}
              </button>
            {/each}
            <div class="palette-other">
              {#if chosen.size}
                <button disabled={busy} onclick={() => apply(null)}>{t('forms.notThisForm')}</button>
                <button disabled={busy} onclick={() => flag('crop')}>{t('issue.crop.title')}</button>
                {#if correcting}
                  <form class="correct-char" onsubmit={event => { event.preventDefault(); flag('character') }}>
                    <input bind:value={correction} maxlength="4" placeholder={t('forms.actual.placeholder')} aria-label={t('forms.actual.aria')} />
                    <button disabled={busy}>{t('forms.report')}</button>
                  </form>
                {:else}<button disabled={busy} onclick={() => correcting = true}>{t('forms.wrongCharacter')}</button>{/if}
                <button disabled={busy} onclick={() => apply(null, 'inherit')}>{t('forms.followCluster')} <kbd>⌫</kbd></button>
              {:else}
                <button disabled={busy || !cluster?.form} onclick={() => apply(null)}>{t('forms.clearCluster')} <kbd>⌫</kbd></button>
              {/if}
            </div>
          </div>
        </div>

        {#if open}
          <div class="cluster-members">
            <div class="members-heading">
              <button class="quiet-link" onclick={close}>{t('forms.allClusters')}</button>
              <h3>{cluster.label} <small>{t('forms.glyphs.count', { count: total || cluster.count })}</small></h3>
              <label class="split-control">{t('forms.splitInto')}
                <select value={splitK} onchange={event => divide(Number(event.currentTarget.value))}>
                  <option value={0}>—</option>{#each [2, 3, 4, 5, 6, 8] as k (k)}<option value={k}>{k}</option>{/each}
                </select>
              </label>
              {#if !splitK}<div class="filter-tabs" role="group" aria-label={t('forms.order.label')}>
                <button class:active={order === 'typical'} aria-pressed={order === 'typical'} onclick={() => reorder('typical')}>{t('forms.order.typical')}</button>
                <button class:active={order === 'unusual'} aria-pressed={order === 'unusual'} onclick={() => reorder('unusual')}>{t('forms.order.unusual')}</button>
              </div>{/if}
              {#if cluster.form}<span class="cluster-form">{cluster.form} <small>{byForm.get(cluster.form)?.jibo ?? ''}</small></span>{/if}
              {#if chosen.size}<button class="quiet-link" onclick={() => chosen = new Set()}>{t('forms.clearSelection')}</button>{/if}
            </div>
            {#if splitK && loadingSplit}
              {#each Array(2) as _, g (g)}<section class="split-group" aria-hidden="true"><div class="member-grid">{#each Array(12) as _, i (i)}<span class="member shimmer"></span>{/each}</div></section>{/each}
            {:else if splitK}
              {#each groups as group, g (g)}
                <section class="split-group">
                  <header><strong>{t('forms.group', { number: g + 1 })}</strong><span>{t('forms.glyphs.count', { count: group.count })}</span>
                    <button class="quiet-link" onclick={() => selectGroup(group)}>{group.ids.every(id => chosen.has(id)) ? t('forms.selectAll.deselect', { count: group.count }) : t('forms.selectAll.select', { count: group.count })}</button></header>
                  <div class="member-grid">
                    {#each group.items as glyph (glyph.id)}
                      <button class="member" class:selected={chosen.has(glyph.id)} class:own={glyph.basis === 'form_glyph'}
                              aria-pressed={chosen.has(glyph.id)} onclick={() => toggleId(glyph.id)} title={glyph.id}>
                        {#if glyph.image}<img class="glyph-image" src={glyph.image} alt="" loading="lazy" use:settle />{/if}
                        {#if glyph.reported}<span class="member-flag" title={t('forms.reported', { reason: glyph.reported })}>{glyph.character ?? '⚠'}</span>
                        {:else if glyph.basis === 'form_glyph'}<span class="member-form">{glyph.form ?? '×'}</span>{/if}
                      </button>
                    {/each}
                  </div>
                  {#if group.count > group.items.length}<p class="split-more">{t('forms.moreLikeThese', { count: group.count - group.items.length })}</p>{/if}
                </section>
              {/each}
            {:else}
            <div class="member-grid" aria-busy={loadingMembers}>
              {#if loadingMembers}{#each Array(Math.min(cluster.count, 36)) as _, i (i)}<span class="member shimmer" aria-hidden="true"></span>{/each}{/if}
              {#each glyphs as glyph, i (glyph.id)}
                <button class="member" class:selected={chosen.has(glyph.id)} class:own={glyph.basis === 'form_glyph'}
                        aria-pressed={chosen.has(glyph.id)} onclick={event => toggle(i, event)} title={glyph.id}>
                  {#if glyph.image}<img class="glyph-image" src={glyph.image} alt="" loading="lazy" use:settle />{/if}
                  {#if glyph.reported}<span class="member-flag" title={t('forms.reported', { reason: glyph.reported })}>{glyph.character ?? '⚠'}</span>
                  {:else if glyph.basis === 'form_glyph'}<span class="member-form">{glyph.form ?? '×'}</span>{/if}
                </button>
              {/each}
            </div>
            {#if glyphs.length < total}<div class="load-more"><button onclick={more}>{t('forms.showMore', { count: total - glyphs.length })}</button></div>{/if}
            {/if}
          </div>
        {:else}
          <div class="forms-toolbar">
            <p class="keyboard-hint forms-keys">{t('forms.keyboardHint')}</p>
            <button class="review-start" onclick={() => reviewing = true}>{t('forms.review.start')} <kbd>R</kbd></button>
            <div class="filter-tabs" role="group" aria-label={t('forms.clusterOrder.label')}>
              <button class:active={arrange === 'shape'} aria-pressed={arrange === 'shape'} onclick={() => rearrange('shape')}>{t('forms.arrange.shape')}</button>
              <button class:active={arrange === 'size'} aria-pressed={arrange === 'size'} onclick={() => rearrange('size')}>{t('forms.arrange.size')}</button>
              <button class:active={openFirst} aria-pressed={openFirst} onclick={toggleOpenFirst}>{t('forms.unassignedFirst')}</button>
            </div>
          </div>
          <ol class="cluster-grid">
            {#each current.items as c, i (c.id)}
              <li class="form-cluster" class:active={i === active} class:picked={picked.has(c.id)} class:assigned={c.form}>
                <button class="cluster-select" onclick={event => choose(i, event)} ondblclick={() => show(i)} aria-pressed={i === active || picked.has(c.id)}>
                  <span class="cluster-head">
                    <strong lang="ja">{c.label}</strong><span>{number(c.count)}</span>
                    {#if c.form}<span class="cluster-form"><span class="inline-glyph">{c.form}</span> {byForm.get(c.form)?.jibo ?? ''}</span>
                    {:else if c.assigned}<span class="cluster-open">{around('forms.haveForm', 'glyph', { count: c.assigned })[0]}<span class="inline-glyph">{c.majority}</span>{around('forms.haveForm', 'glyph', { count: c.assigned })[1]}</span>
                    {:else}<span class="cluster-open">{t('corpus.unassigned')}</span>{/if}
                  </span>
                  <span class="cluster-samples">{#each c.representatives as r (r.id)}{#if r.image}<img class="glyph-image" src={r.image} alt="" loading="lazy" use:settle />{/if}{/each}</span>
                </button>
                <span class="cluster-foot">
                  {#if c.exceptions}<small>{t('forms.setIndividually', { count: c.exceptions })}</small>{/if}
                  <button class="quiet-link" onclick={() => show(i)}>{t('forms.open', { count: c.count })}</button>
                </span>
              </li>
            {/each}
          </ol>
        {/if}
        {/if}
      </div>
    {/if}
  </div>
  {#if notice}<div class="save-toast" role="status">{notice}</div>{/if}
</section>

<style>
  .forms{padding:52px 4.4vw 60px;max-width:1920px;margin:auto}
  .forms-heading{padding-bottom:30px}
  .forms-heading h1{font-size:clamp(40px,5vw,76px);letter-spacing:-.06em;font-weight:500;line-height:1.15;margin-top:13px}
  .forms-lede{max-width:640px;font-size:13px;line-height:1.6;color:var(--muted);margin-top:8px}
  .forms-layout{display:grid;grid-template-columns:220px minmax(0,1fr);gap:32px;align-items:start}
  .family-list{position:sticky;top:16px;max-height:calc(100dvh - 32px);display:flex;flex-direction:column;gap:10px}
  .family-list input{width:100%;font-size:13px;padding:9px 11px}
  .family-list ol{list-style:none;margin:0;padding:0;overflow:auto;border-top:1px solid var(--line)}
  .family-list button{display:grid;grid-template-columns:40px 1fr;grid-template-rows:auto 3px;gap:4px 10px;width:100%;border:0;border-bottom:1px solid var(--line);border-radius:0;background:transparent;padding:9px 6px;text-align:left}
  .family-list button.current{background:var(--accent-light)}
  .family-char{grid-row:1/3;font-size:24px;line-height:1.3;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
  .family-meta{display:flex;justify-content:space-between;font-size:12px;font-variant-numeric:tabular-nums}
  .family-meta small{font-size:10px;color:var(--muted)}
  .family-progress{display:flex;background:#ececef;border-radius:2px;overflow:hidden}.family-progress i{display:block;height:100%;background:var(--accent)}.family-progress i.rejected{background:var(--wrong);opacity:.55}
  .family-title{display:flex;align-items:baseline;gap:18px;border-bottom:1px solid var(--line);padding-bottom:12px}
  .family-title h2{font-size:48px;font-weight:500;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
  .family-title p{font-size:12px;color:var(--muted)}.family-title strong{color:var(--ink);font-weight:500}
  .form-palette{position:sticky;top:0;z-index:3;background:#fafafaf2;backdrop-filter:blur(12px);padding:14px 0;border-bottom:1px solid var(--line)}
  .palette-target{font-size:12px;color:var(--muted);margin-bottom:10px}.palette-target strong{color:var(--ink);font-weight:500}
  .palette-forms{display:flex;flex-wrap:wrap;gap:6px;align-items:stretch}
  .form-choice{position:relative;display:flex;flex-direction:column;align-items:center;gap:2px;min-width:66px;padding:8px 8px 6px;background:#fff}
  .form-choice:not(:disabled):hover{border-color:var(--accent);background:#f6f4ff}
  .form-source{font-size:12px;min-height:16px;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
  .form-choice small{font-size:8px;color:var(--muted);font-family:ui-monospace,monospace}
  .form-choice kbd{position:absolute;top:4px;right:5px;font-size:8px;color:#a0a0a7;font-family:ui-monospace,monospace}
  .palette-other{display:flex;flex-wrap:wrap;gap:6px;margin-left:auto;align-content:flex-start;max-width:260px}.palette-other button{font-size:11px;padding:8px 11px}
  .palette-other kbd{font-size:9px;color:var(--muted)}
  .forms-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:10px 0 12px}.forms-keys{margin:0}
  .review-start{margin-left:auto;font-size:12px;padding:8px 13px;background:var(--ink);color:#fff;border-color:var(--ink)}.review-start kbd{font-size:9px;opacity:.7}
  .form-cluster.picked{border-color:var(--accent);background:#f3f1ff}
  .cluster-grid{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}
  .form-cluster{border:1.5px solid var(--line);border-radius:9px;background:#fff;overflow:hidden}
  .form-cluster.active{border-color:var(--accent);box-shadow:0 0 0 3px #6356e51f}
  .form-cluster.assigned{background:#fbfbfe}
  .cluster-select{display:block;width:100%;border:0;border-radius:0;background:transparent;padding:12px 12px 8px;text-align:left}
  .cluster-head{display:flex;align-items:center;gap:10px;font-size:12px;margin-bottom:8px}
  .cluster-head>span:nth-child(2){color:var(--muted);font-variant-numeric:tabular-nums}
  .cluster-form{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:12px;color:var(--accent);background:var(--accent-light);border-radius:14px;padding:2px 10px}
  .cluster-form .inline-glyph{font-size:18px}
  .cluster-open{margin-left:auto;font-size:10px;color:var(--muted)}
  .cluster-samples{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:4px}
  .cluster-samples img{aspect-ratio:1;border-radius:3px;padding:3px}
  .cluster-foot{display:flex;align-items:center;justify-content:space-between;padding:0 12px 10px;font-size:10px;color:var(--muted)}
  .cluster-foot .quiet-link{margin-left:auto;font-size:11px}
  .cluster-members{margin-top:16px}
  .members-heading{display:flex;align-items:center;gap:18px;margin-bottom:12px}
  .members-heading h3{font-size:16px;font-weight:500}.members-heading small{font-size:11px;color:var(--muted);font-weight:400;margin-left:6px}
  .member-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));gap:4px}
  .family-skeleton{display:grid;grid-template-columns:40px 1fr;gap:10px;padding:12px 6px;border-bottom:1px solid var(--line)}.family-skeleton span{height:22px;border-radius:4px}
  .title-skeleton{width:52px;height:56px;border-radius:6px}.line-skeleton{display:block;width:220px;height:12px;border-radius:4px}
  .palette-skeleton{display:flex;gap:6px;padding:14px 0;border-bottom:1px solid var(--line)}.palette-skeleton span{width:66px;height:82px;border-radius:7px}
  .cluster-skeleton{padding:12px 12px 16px}.cluster-skeleton .line-skeleton{width:120px;margin-bottom:10px}.cluster-skeleton .cluster-samples span{aspect-ratio:1;border-radius:3px}
  span.member{display:block}
  .cluster-samples img:not(.pending){background:#f4f4f5}
  .member{position:relative;aspect-ratio:1;padding:6px;border:1.5px solid transparent;border-radius:5px;background:#f1f1f3}
  .member.selected{border-color:var(--accent);background:#e7e3ff}
  .member.own{border-style:dashed;border-color:#b3acd9}
  .split-control{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)}.split-control select{font-size:12px;padding:4px 6px;border:1px solid var(--line);border-radius:5px;background:#fff}
  .split-group{margin-bottom:22px}.split-group header{display:flex;align-items:baseline;gap:12px;font-size:13px;margin-bottom:8px}.split-group header span{color:var(--muted);font-size:11px}.split-more{font-size:11px;color:var(--muted);margin-top:6px}
  .member-flag{position:absolute;top:3px;right:5px;font-size:12px;color:var(--wrong)}
  .correct-char{display:flex;gap:4px}.correct-char input{width:64px;padding:6px 8px;font-size:14px}.correct-char button{font-size:11px;padding:6px 9px}
  .member-form{position:absolute;top:3px;right:5px;font-size:14px;color:var(--accent);font-family:"Kureedo Kata","Noto Serif Hentaigana",system-ui,sans-serif}
  @media(max-width:900px){.forms-layout{grid-template-columns:1fr}.family-list{position:static;max-height:260px}.cluster-grid{grid-template-columns:1fr}}
  @media(max-width:700px){.forms{padding:30px 16px 40px}.form-choice{min-width:54px}.palette-other{flex-direction:row;margin-left:0;width:100%}}
</style>
