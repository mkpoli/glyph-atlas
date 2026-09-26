<script>
  import { onMount, tick, untrack } from 'svelte'
  import { replaceState } from '$app/navigation'
  import { page } from '$app/state'
  import { productionLabel } from '../components/ProductionBadge.svelte'
  import VisualGroups from '../components/VisualGroups.svelte'
  import { isUnassigned, writtenLabel, visualGroup, matchesVisualGroup, graphemeChar } from '../lib/identity.js'
  import Glyph from '../components/Glyph.svelte'
  import ImageStyleToggle from '../components/ImageStyleToggle.svelte'
  import CharacterSearch from '../components/CharacterSearch.svelte'
  import CharacterChips from '../components/CharacterChips.svelte'
  import WorkFilter from '../components/WorkFilter.svelte'
  import GraphemeGrid from '../components/GraphemeGrid.svelte'
  import { catalogue, character, request, randomSeed, number, formatSerial, stored, remember } from '../lib/client.js'
  import { character as layerCharacter, occurrences, candidates as layerCandidates, gallery as layerGallery } from '../lib/layers.js'
  import { t, around, localName, locale, localize, delocalize } from '../lib/i18n.svelte.js'
  import { characterAddress, scopeFor, unslug } from '../lib/gallery.js'
  // `initial` is the collection page the server rendered: the seed it shuffled with, the collection's
  // rows, the corpus sample and the progress line. `gallery` is a character's page instead
  // (`lib/gallery.js`). Without either the view loads what it shows itself.
  // `addressed` is the collection and character pages, whose address this view keeps; the Flagged view
  // and the collection behind a crop's dialog leave theirs alone.
  // `shown` is the character on show, for the page's title; the view changes it in place.
  let { flagged = false, addressed = false, inspect, ink = 'original', onink = () => {}, onprogress = () => {}, initial = null, gallery = null, shown = $bindable() } = $props()
  const first = untrack(() => initial), opened = untrack(() => gallery)
  let data = $state(first?.result ?? (opened ? { query: opened.picked.char, total: opened.total, available: opened.available,
    categories: opened.summary?.categories ?? [], documents: opened.summary?.documents ?? [], counts: opened.summary?.counts ?? {} } : null))
  let items = $state(first?.result.items ?? []), error = $state(''), loading = $state(!first && !opened)
  let grapheme = $state(''), work = $state(''), offset = $state(0), seed = $state(first?.seed ?? randomSeed())
  let query = $state(opened?.picked.char ?? '')
  let choosing = $state(false), catalogueRequest = null
  let filter = $state('all'), requestId = 0, closed = false
  // The Flagged view hides crops already reviewed in the inspector by default; the choice is
  // remembered across visits.
  let showReported = $state(stored('atlas.showReported', false))
  function toggleReported() {
    showReported = !showReported
    remember('atlas.showReported', showReported)
    offset = 0; load()
  }
  // A picked character has a gallery of its own: the occurrences this collection holds and the
  // located glyphs the corpus index knows about. They are separate lists with separate paging —
  // different services page them — and they are merged for display only.
  let picked = $state(opened?.picked ?? null), expand = $state(opened?.expand ?? 'none'), local = $state(opened?.local ?? []), corpus = $state(opened?.corpus ?? [])
  let visual = $state(opened?.visual ?? ''), analysis = $state(opened?.analysis ?? null), familyTotal = $state(opened?.familyTotal ?? null), unassignedCount = $state(opened?.unassignedCount ?? null)
  let corpusTotal = $state(opened?.corpusTotal ?? 0), corpusOffset = $state(opened?.corpus.length ?? 0), pickId = 0, corpusFault = $state(opened?.corpusFault ?? null)
  $effect(() => { shown = picked?.code_point ? { char: picked.char, code_point: picked.code_point } : null })
  /**
   * The address says what is on show, so it can be shared and reloaded: a character's page with its
   * scope and visual group, or the collection. The view changes what it
   * shows in place and rewrites the address to match, so typing never leaves the page.
   */
  function showInAddress() {
    if (!addressed) return
    const [path, search = ''] = (picked?.code_point
      ? characterAddress(picked.code_point, { scope: scopeFor(expand, picked), visual }) : '/').split('?')
    const target = localize(path) + (search && '?' + search)
    // A shallow rewrite leaves `page.url` as it was loaded, so the address bar is what is compared.
    if (location.pathname + location.search !== target) replaceState(target, page.state)
  }
  // The unfiltered homepage mixes a bounded corpus sample with the collection's own rows, so the
  // first page is not one source's leftovers: the sample is normalised by the same adapter and
  // deduplicated against what is already on the page.
  let sample = $state(first ? sampleRows(first.sample, first.result.items) : []), sampleFault = $state(first?.sample ? (first.sample.status === 'ok' ? null : first.sample.status) : null)
  let collection = $state(first?.collection ?? null)
  async function readCollection() {
    try { collection = await request('/atlas/collection/status') } catch { /* retry on the next interval */ }
  }
  const categories = $derived((data?.categories ?? []).filter(c => !flagged || c.flagged || c.hard))
  /** A grapheme key (`U+4EEE`, or a label's own code points) as the text it names. */
  const charOf = key => /^U\+[0-9A-F]{4,6}( U\+[0-9A-F]{4,6})*$/i.test(key)
    ? key.split(' ').map(point => String.fromCodePoint(parseInt(point.slice(2), 16))).join('') : key
  // The readings the catalogue counts, gathered under their graphemes: 仮 and 假 are one tile.
  const graphemes = $derived.by(() => {
    const groups = new Map()
    for (const c of categories) {
      const key = c.grapheme ?? c.label
      const group = groups.get(key) ?? { key, char: charOf(key), count: 0, members: [] }
      const count = flagged ? c.flagged + c.hard : c.total
      group.members.push({ label: c.label, count }); group.count += count
      groups.set(key, group)
    }
    for (const group of groups.values()) group.members.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
    return [...groups.values()].sort((a, b) => b.count - a.count || a.key.localeCompare(b.key))
  })
  const chosenGrapheme = $derived(graphemes.find(group => group.key === grapheme))
  /** The grapheme a corpus row is filed under, as the catalogue files a label. */
  const codesOf = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
  const rowGrapheme = row => row.grapheme?.code_point ?? row.grapheme ?? codesOf(row.label ?? '')
  const works = $derived((data?.documents ?? []).filter(w => !flagged || w.flagged || w.hard)
    .map(w => ({ ...w, count: flagged ? w.flagged + w.hard : w.total })))
  /** What a record says about its own reliability, in one badge: withheld, machine or confirmed.
   *
   * The words come from the record's `repair` block — `withheld`, `reliable`, `verified`, `reason` —
   * and nothing is claimed that the record does not state: a machine alignment with no human review
   * reads `machine`, a withheld record reads `withheld`, and only `verified` reads `checked`.
   */
  function repairOf(item) {
    const repair = item.repair
    if (repair) {
      if (item.withheld || repair.withheld) return { kind: 'withheld', label: 'withheld', reason: repair.reason ?? t('repair.reason.heldBack') }
      if (repair.verified) return { kind: 'verified', label: 'checked', reason: repair.reason ?? t('repair.reason.personChecked') }
      if (repair.machine || repair.reliable === false) {
        return { kind: 'machine', label: 'machine', reason: repair.reason ?? t('repair.reason.machineProposed') }
      }
      return null
    }
    // The corpus states its own case: a lead nobody has confirmed is not a verified example.
    if (item.origin === 'corpus' || item.requires_review) return { kind: 'machine', label: 'unconfirmed', reason: item.licence_note ?? t('repair.reason.corpusUnconfirmed') }
    if (item.machine) return { kind: 'machine', label: 'machine', reason: t('repair.reason.machineProposed') }
    return null
  }
  /** The label a tile shows. `identity.js` names an unassigned crop and a group without a label in
   * English, because it also runs outside the interface; the interface says it in the reader's language. */
  const shownLabel = item => isUnassigned(item) ? t('corpus.unassigned') : item.label
  // The grapheme beside the written form: always when the form is unassigned, else only when it differs.
  const shownGrapheme = item => { const g = graphemeChar(item); return g && (isUnassigned(item) || g !== item.label) ? g : null }
  const groupLabel = group => group.id === 'unassigned' ? t('corpus.unassigned')
    : group.label === 'Similar forms' ? t('explore.similarForms') : group.label
  /** Whether a crop still waits for a person: flagged, or hard to read after two reviewers skipped it. */
  const waiting = state => state === 'flagged' || state === 'hard'
  /** One state per tile: checked, flagged or hard get a corner badge and outline; withheld and plain a dot. */
  function tileState(item) {
    if (item.state === 'checked' || item.state === 'flagged' || item.state === 'hard') return item.state
    const kind = repairOf(item)?.kind
    return kind === 'verified' ? 'checked' : kind === 'withheld' ? 'withheld' : 'plain'
  }
  /** What the tile's hover panel lists: the reading when it differs, the material, how far the record
   * has been checked, where it comes from, the work and page, and the holder. */
  function tileDetails(item) {
    const repair = repairOf(item)
    const state = item.state === 'checked' ? t('state.checked') : item.state === 'flagged' ? t('state.flagged') : item.state === 'hard' ? t('state.hard')
      : repair?.kind === 'withheld' ? t('tile.withheldReason', { reason: repair.reason }) : repair?.kind === 'verified' ? t('state.checked')
      : repair?.label === 'unconfirmed' ? t('tile.notYetConfirmed') : repair ? t('tile.machineAligned') : null
    const origin = item.origin === 'corpus' ? ((item.source?.corpus ?? item.corpus) === 'codh-full' ? t('tile.origin.codh') : t('tile.origin.corpus')) : null
    const work = typeof item.source === 'string' ? item.source : (item.source?.title ?? item.title)
    const page = item.page_number ? t('tile.page', { page: item.page_number }) : null
    return [item.reading && item.reading !== item.label ? item.reading : null, productionLabel(item), state, origin,
      [work, page].filter(Boolean).join(' · ') || null, typeof item.source === 'string' ? item.holder : (item.source?.holder ?? item.holder)]
      .filter(Boolean)
  }
  // The units the inspector may step through: the character's own records when a gallery is open, the
  // collection's rows otherwise. It is never empty on the ordinary homepage, so Next keeps working.
  const visibleLocal = $derived(local.filter(item => matchesVisualGroup(item, visual)))
  const editable = $derived((picked ? visibleLocal : items).filter(item => (item.origin ?? 'collection') === 'collection'))
  /** The same record from two services: an exact id match is the only dedup that is certain. */
  function sameInk(unit, lead) {
    return Boolean(unit?.id) && (unit.id === lead.id || unit.id === lead.identity_key)
  }
  const corpusOnly = $derived(picked ? corpus.filter(lead => !visibleLocal.some(unit => sameInk(unit, lead))) : [])
  const homeCorpus = $derived(sample.filter(lead => !items.some(unit => sameInk(unit, lead))))
  const baseDisplay = $derived(choosing ? [] : picked ? [...visibleLocal, ...corpusOnly]
    : [...items.flatMap((item, index) => homeCorpus[index] ? [item, homeCorpus[index]] : [item]),
       ...homeCorpus.slice(items.length)])
  function groupOrder(item) {
    const group = visualGroup(item)
    const index = (analysis?.groups ?? []).findIndex(entry => entry.id === group.id)
    return index >= 0 ? index : group.id === 'unassigned' ? Number.MAX_SAFE_INTEGER : 10000
  }
  const display = $derived(picked && expand === 'grapheme'
    ? [...baseDisplay].sort((a, b) => groupOrder(a) - groupOrder(b) || visualGroup(a).id.localeCompare(visualGroup(b).id)) : baseDisplay)
  const settled = $derived(data && data.query === query ? data : null)
  // A character no font on the machine can draw is a blank box, which would leave a reviewer unable
  // to see what they searched for. The code point is shown beside it when it is outside the
  // basic plane, where coverage is the exception rather than the rule.
  const readable = $derived(query && [...query].some(c => c.codePointAt(0) > 0xffff)
    ? `${query} (${[...query].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')})`
    : query)

  async function load(append = false) {
    choosing = false
    catalogueRequest?.abort()
    if (!append) showInAddress()
    const id = ++requestId; loading = true; error = ''
    try {
      if (picked) {
        if (!append) { local = []; corpus = []; corpusTotal = 0; corpusOffset = 0 }
        // Local records page by their own count, and they are shown before the corpus is asked:
        // a corpus that cannot answer must not hide the records this collection does hold.
        const found = await occurrences(picked.code_point, { expand, limit: 60, offset: append ? local.length : 0 })
        if (closed || id !== requestId) return
        const rows = found.items.map(item => ({ ...item, origin: 'collection' }))
        local = append ? [...local, ...rows] : rows
        data = { ...(data ?? {}), query: picked.char, total: found.counts.total, available: found.counts.exact_total,
                 categories: data?.categories ?? [], documents: data?.documents ?? [], counts: data?.counts ?? {} }
        // The chips come from the card, and the card says which widening is in force: refetch it so a
        // chip that was just switched on reads as on.
        layerCharacter(picked.code_point, expand)
          .then(card => { if (!closed && id === requestId) picked = { ...picked, ...card } })
          .catch(() => {})
        if (append) return
        // The corpus leads are fetched whole, because their service pins located anchors first and a
        // page of text hits would bury them.
        corpusFault = null
        try {
          const leads = await layerCandidates(picked.code_point, 60, 0,
            { scope: expand === 'grapheme' ? 'grapheme' : 'character', visual_group: visual || undefined })
          if (closed || id !== requestId) return
          corpus = (leads.glyph_items ?? []).map(item => ({ ...item, label: writtenLabel(item), origin: 'corpus' }))
          corpusTotal = leads.glyphs ?? 0; corpusOffset = corpus.length
          analysis = leads.visual_analysis ?? picked.visual_analysis ?? null
          familyTotal = leads.family_total ?? null; unassignedCount = leads.unassigned_count ?? null
        } catch (e) {
          if (!closed && id === requestId) corpusFault = e.status === 502 ? 'error' : 'not-loaded'
        }
        return
      }
      catalogueRequest = new AbortController()
      // The corpus half is requested alongside the crops, and deduplicated against them once both are in.
      const sampled = append ? null : requestSample()
      const result = await catalogue({ grapheme, document: work, q: query, group: filter, state: flagged ? 'attention' : 'all',
        reported: flagged ? (showReported ? 'show' : 'hide') : null, seed, offset, limit: 60 }, { signal: catalogueRequest.signal, priority: 'low' })
      if (closed || id !== requestId) return
      data = result; items = append ? [...items, ...result.items] : result.items
      if (!append) await showSample(id, sampled, result.items)
    } catch (e) { if (!closed && id === requestId && e.name !== 'AbortError') error = e.message }
    finally { if (!closed && id === requestId) loading = false }
  }
  /** The homepage's corpus half: bounded, deduplicated against the local rows, never a scan. */
  function requestSample() {
    const bare = !query && !picked && filter === 'all' && !grapheme && !work
    const pending = flagged ? request('/atlas/corpus/reviews?state=flagged') : bare ? layerGallery(60, seed) : null
    // Settled here, so a failure waits for `showSample` instead of surfacing as unhandled.
    return pending?.then(page => ({ page }), error => ({ error })) ?? null
  }
  async function showSample(id, sampled, localRows) {
    if (flagged) {
      const { page: result, error } = await sampled
      if (error) throw error
      if (closed || id !== requestId) return
      // A corpus glyph belongs to no work of this collection, so choosing a work leaves them out.
      sample = work ? [] : result.items.filter(row => (!query || row.label.includes(query)) && (!grapheme || rowGrapheme(row) === grapheme))
      sampleFault = null
      return
    }
    if (!sampled) { sample = []; sampleFault = null; return }
    try {
      const { page, error } = await sampled
      if (error) throw error
      if (closed || id !== requestId) return
      sample = sampleRows(page, localRows)
      sampleFault = page.status === 'ok' ? null : page.status
    } catch (e) {
      if (!closed && id === requestId) { sample = []; sampleFault = e.status === 502 ? 'error' : 'not-loaded' }
    }
  }

  /** A gallery page as tiles: images this site may show, none the collection's own rows already hold. */
  function sampleRows(page, localRows) {
    const known = new Set(localRows.flatMap(row => [row.id, row.identity_key].filter(Boolean)))
    return (page?.items ?? [])
      .filter(row => row.proxyable && row.image)
      .filter(row => !known.has(row.id) && !known.has(row.identity_key))
      .map(row => ({ ...row, label: writtenLabel(row), origin: 'corpus' }))
  }

  let searchTimer
  function seek(value) {
    query = value; visual = ''; analysis = null; familyTotal = null; unassignedCount = null
    // Typing is leaving the character that was chosen: its gallery, its widening and any answer still
    // on its way belong to the query that is being replaced.
    pickId += 1; requestId += 1
    catalogueRequest?.abort()
    if (picked || expand !== 'none') { picked = null; expand = 'none'; local = []; corpus = []; corpusTotal = 0 }
    corpusOffset = 0
    // A direct character search answers its own question, so it drops the grapheme, type and work filters
    // rather than intersecting with them: with シ selected, searching ア used to answer nothing and
    // say there was no such occurrence, when the filter was what excluded it.
    if (value) { grapheme = ''; filter = 'all'; work = '' }
    clearTimeout(searchTimer)
    // Readings such as トモ ask the candidate index first. Scanning the crop catalogue for every
    // intermediate spelling only competes with the list the reader is trying to choose from.
    choosing = [...value.trim()].length > 1 && !/^(U\+[0-9a-f]{4,6})(\s+U\+[0-9a-f]{4,6})*$/i.test(value.trim())
    // The character that was on show is gone as soon as typing starts, and the address says so.
    showInAddress()
    if (choosing) { loading = false; return }
    // Typing one code point at a time would otherwise search the intermediate text, and a
    // supplementary character arrives as two UTF-16 units while it is being entered.
    searchTimer = setTimeout(() => { offset = 0; submitQuery(value) }, 220)
  }
  function submitQuery(value = query) {
    const term = value.trim()
    if (/^U\+[0-9a-f]{4,6}$/i.test(term)) return pick({ code_point: term.toUpperCase(), char: term })
    if ([...term].length === 1) return pick({ code_point: 'U+' + term.codePointAt(0).toString(16).toUpperCase().padStart(4, '0'), char: term })
    load()
  }
  function clearQuery() {
    choosing = false
    clearTimeout(searchTimer); pickId += 1
    visual = ''; analysis = null; familyTotal = null; unassignedCount = null
    query = ''; picked = null; expand = 'none'; local = []; corpus = []; corpusTotal = 0; corpusOffset = 0; offset = 0; load()
  }
  /** The next page of corpus leads, kept on its own offset: it is paged by another service. */
  async function moreCorpus() {
    if (!picked || loading) return
    // The character and the generation are captured: a page that arrives after the reader has picked
    // something else belongs to the old query and is dropped rather than appended to the new one.
    const target = picked.code_point
    const current = ++pickId
    const id = ++requestId
    loading = true
    try {
      const page = await layerCandidates(target, 60, corpusOffset,
        { scope: expand === 'grapheme' ? 'grapheme' : 'character', visual_group: visual || undefined })
      if (closed || current !== pickId || id !== requestId || picked?.code_point !== target) return
      const rows = (page.glyph_items ?? []).map(item => ({ ...item, label: writtenLabel(item), origin: 'corpus' }))
      corpusOffset += rows.length
      corpus = [...new Map([...corpus, ...rows].map(row => [row.id, row])).values()]
      corpusTotal = page.glyphs ?? corpusTotal
    } catch (e) {
      if (!closed && current === pickId && id === requestId) corpusFault = e.status === 502 ? 'error' : 'not-loaded'
    } finally { if (!closed && id === requestId) loading = false }
  }
  // Choosing a candidate is choosing what to look at: the grid becomes that character's gallery.
  async function pick(item, exact = false) {
    let expansionWillLoad = false
    choosing = false
    clearTimeout(searchTimer)
    requestId += 1
    const current = ++pickId
    picked = null
    // A chip passes a code point and a candidate row passes itself; whichever it is, the card is
    // given the fields the chips read so a half-known character never renders as undefined.
    const bare = { char: '', characters: [], derived: [], jibo: [], expansions: [], candidates: null }
    query = item.char ?? ''; grapheme = ''; filter = 'all'; work = ''; offset = 0; expand = 'none'
    local = []; corpus = []; corpusTotal = 0; corpusOffset = 0; corpusFault = null
    visual = ''; analysis = null; familyTotal = null; unassignedCount = null
    if (item.code_point) {
      try {
        const card = await layerCharacter(item.code_point, 'none')
        if (closed || current !== pickId) return   // a newer choice owns the gallery now
        picked = { ...bare, ...item, ...card }; query = card.char
        const nextExpand = !exact && (card.default_scope === 'grapheme' || card.candidates?.requires_family_scope) ? 'grapheme' : 'none'
        expansionWillLoad = expand !== nextExpand
        expand = nextExpand
        analysis = card.visual_analysis ?? null
      } catch { if (!closed && current === pickId) picked = { ...bare, ...item } }
    }
    if (!closed && current === pickId) { if (!picked) picked = { ...bare, ...item }; if (!expansionWillLoad) load() }
  }
  function fitsGallery(row) {
    if (!picked) return true
    if (!matchesVisualGroup(row, visual)) return false
    if (expand !== 'grapheme') return !isUnassigned(row) && writtenLabel(row) === picked.char
    if (isUnassigned(row)) return (row.grapheme?.code_point ?? row.grapheme) === picked.grapheme?.code_point
    return (picked.grapheme?.members ?? [picked]).some(member => member.char === writtenLabel(row))
  }
  /** A save can take its tile off the page (Needs fixing drops a cleared crop). Focus then moves to the
   * tile that took its place, so a keyboard reviewer keeps their position in the grid. */
  async function keepPlace(id, apply) {
    const tiles = () => [...document.querySelectorAll('.glyph-grid .glyph-tile')]
    const at = tiles().findIndex(tile => (tile.dataset.unit ?? tile.dataset.corpus) === id)
    apply()
    await tick()
    if (at < 0 || document.activeElement !== document.body) return
    const left = tiles()
    left[Math.min(at, left.length - 1)]?.focus()
  }
  async function updateItem(id, result) {
    if (result?.origin === 'corpus') {
      const replace = row => {
        if (row.id !== id) return [row]
        const updated = { ...row, ...result }
        return (flagged && !waiting(result.state)) || !fitsGallery(updated) ? [] : [updated]
      }
      keepPlace(id, () => { corpus = corpus.flatMap(replace); sample = sample.flatMap(replace) })
      return
    }
    try {
      const updated = await character(id)
      // The tile that was just saved is in one of two lists: the character's gallery, or the
      // collection's own rows. Updating the wrong one leaves the tile showing its old state.
      const replace = item => item.id !== id ? [item] : (flagged && !waiting(updated.state)) || !fitsGallery(updated) ? [] : [updated]
      keepPlace(id, () => { if (picked) local = local.flatMap(replace); else items = items.flatMap(replace) })
      const summary = await catalogue({ grapheme, document: work, q: query, group: filter, state: flagged ? 'attention' : 'all',
        reported: flagged ? (showReported ? 'show' : 'hide') : null, limit: 1 })
      if (!closed) data = { ...data, counts: summary.counts, categories: summary.categories, documents: summary.documents, total: summary.total, available: summary.available, reported_count: summary.reported_count }
    } catch (e) { if (!closed) error = e.message }
  }
  function select(value) { grapheme = value; offset = 0; load() }
  function shuffle() { seed = randomSeed(); offset = 0; load() }
  /**
   * Back or Forward to an address this view rewrote loads the page as it was first loaded, while the
   * address bar keeps what the view had moved on to. The view follows the address bar.
   */
  function followAddress() {
    const { path } = delocalize(location.pathname)
    const code = path.startsWith('/character/') ? unslug(path.slice(11)) : null
    if (code && code !== picked?.code_point) pick({ code_point: code }, new URLSearchParams(location.search).get('scope') === 'exact')
    else if (!code && path === '/' && picked) clearQuery()
  }
  onMount(() => { if (!first) readCollection(); if (!first && !opened) load(); if (addressed) followAddress(); const timer = setInterval(readCollection, 30000); return () => { closed = true; clearInterval(timer); clearTimeout(searchTimer); catalogueRequest?.abort() } })
  // Widening is the reader's choice and only it reloads the gallery; picking a character resets the
  // widening itself and loads once through `pick`.
  // The first run is the widening the page opened with, already loaded.
  let shownExpand = untrack(() => expand)
  $effect(() => { const value = expand; if (value === shownExpand) return; shownExpand = value; untrack(() => { visual = ''; if (picked && !closed) load() }) })
</script>

<section class="explore">
  <div class="explore-status">
    <h1 class="visually-hidden">{flagged ? t('explore.heading.flagged') : t('explore.heading.atlas')}</h1>
    {#if !flagged}
      <button class="collection-progress-link" onclick={onprogress}>
        <span class="live-dot"></span> {t('explore.collectionProgress')}
        {#each collection?.sources ?? [] as source}<span>{source.name} <b>{number(source.completed)}</b>{#if source.total} / {number(source.total)}{/if}</span>{/each}
        <span>↗</span>
      </button>
    {/if}
    <div class="collection-meta"><span class="live-dot"></span>{#if picked}<span>{t('explore.meta.glyphs', { count: display.length })}</span><span class="meta-divider">/</span><span>{expand === "grapheme" ? t('explore.meta.characters', { count: picked.grapheme?.character_count ?? 1 }) : t('explore.meta.characters', { count: 1 })}</span>{:else if !flagged && collection?.archive}<span>{t('explore.meta.indexedCrops', { count: collection.archive.character_crops })}</span><span class="meta-divider">/</span><span>{t('explore.meta.worksWithCrops', { count: collection.archive.works_with_crops })}</span>{:else}<span>{t('explore.meta.glyphsTotal', { count: flagged ? (data?.total ?? 0) + sample.length : data?.available })}</span><span class="meta-divider">/</span><span>{t('explore.meta.graphemes', { count: graphemes.length })}</span>{/if}</div>
  </div>
  <div class="collection-toolbar">
    <!-- The box, empty and focused, lists the collection's graphemes; one chosen narrows the grid, and a
         form in a tile's popover opens that form's own gallery. -->
    {#snippet browse(close)}
      <p class="candidate-status">{t('explore.graphemes')}</p>
      <GraphemeGrid groups={graphemes} value={grapheme} onchoose={key => { close(); select(key) }}
                    onform={form => { close(); pick({ code_point: codesOf(form), char: form }, true) }} />
    {/snippet}
    <CharacterSearch bind:value={query} oninput={seek} onselect={pick} {browse}
                     token={grapheme ? charOf(grapheme) : ''} tokenLabel={t('explore.clearGrapheme', { grapheme: charOf(grapheme) })} ontokenclear={() => select('')}
                     onsubmit={() => { clearTimeout(searchTimer); offset = 0; submitQuery() }} />
    <div class="filter-tabs" aria-label={t('explore.filter.label')}>{#each [['all', () => t('explore.filter.all')], ['kana', () => t('explore.filter.kana')], ['kanji', () => t('explore.filter.kanji')], ['hangul', () => t('explore.filter.hangul')], ['gugyeol', () => t('explore.filter.gugyeol')]] as [value, text]}<button class:active={filter === value} onclick={() => { filter = value; offset = 0; load() }}>{text()}</button>{/each}</div>
    <!-- A work narrows the collection's listing; choosing one leaves a picked character's gallery. -->
    <WorkFilter {works} value={work} onchange={value => { work = value; if (picked || query) clearQuery(); else { offset = 0; load() } }} />
    <span class="toolbar-space"></span>
    <ImageStyleToggle {ink} onchange={onink} />
    <!-- A round asks about one written character, so it is offered only for a grapheme of one form. -->
    {#if chosenGrapheme?.members.length === 1 && !flagged}{@const reading = chosenGrapheme.members[0].label}<a class="quiet-link" href={localize('/review') + `?reading=${encodeURIComponent(reading)}`}>{t('explore.reviewReading', { reading })}</a>{/if}
    {#if flagged && data?.reported_count}<button class="quiet-link" onclick={toggleReported}>{showReported ? t('explore.flagged.hideReported') : t('explore.flagged.showReported', { count: data.reported_count })}</button>{/if}
    <button class="shuffle" onclick={shuffle} disabled={loading} aria-label={t('explore.shuffle.aria')}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 6h3c4 0 8 12 12 12h3M17 14l4 4-4 4M3 18h3c1.7 0 3.5-2.3 5-5M14 8c1.5-1.4 2.6-2 4-2h3M17 2l4 4-4 4"/></svg>{t('explore.shuffle')}</button>
  </div>
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load()}>{t('common.retry')}</button></div>{/if}
  {#if picked}
    <CharacterChips card={picked} bind:expand onselect={item => pick({ code_point: item }, true)} />
    {#if expand === 'grapheme'}<VisualGroups {analysis} count={familyTotal} unassigned={unassignedCount} value={visual} onchange={value => { visual = value; load() }} />{/if}
    <p class="find-count" role="status">
      {t('explore.meta.glyphs', { count: display.length })}
      {#if editable.length}<span class="separator">·</span> {t('explore.count.here', { count: editable.length })}{/if}
      {#if corpusOnly.length}<span class="separator">·</span> {t('explore.count.fromCorpus', { count: corpusOnly.length })}{/if}
      {#if corpusFault}<span class="separator">·</span> <span class="corpus-fault" role="status">{t('explore.samplesUnavailable')}</span> <button class="quiet-link" onclick={() => load()}>{t('common.retry')}</button>{/if}
      {#if loading}<span class="find-pending"> …</span>{/if}
    </p>
  {:else if !query && sample.length}
    <p class="find-count" role="status">{t('explore.count.here', { count: items.length })}<span class="separator">·</span> {t('explore.count.fromCorpus', { count: sample.length })}{#if sampleFault}<span class="corpus-fault"> · {sampleFault === 'error' ? t('explore.corpus.error') : t('explore.corpus.notLoaded')}</span>{/if}</p>
  {:else if query && settled}
    <p class="find-count" role="status">{around('explore.occurrencesOf', 'reading', { count: settled.total })[0]}<b>{readable}</b>{around('explore.occurrencesOf', 'reading', { count: settled.total })[1]}{#if loading}<span class="find-pending"> …</span>{/if}</p>
  {/if}
  <div class="glyph-grid" aria-label={flagged ? t('explore.heading.flagged') : t('explore.grid.collection')} aria-busy={loading}>
    {#if loading && !display.length}{#each Array(32) as _}<div class="glyph-skeleton"></div>{/each}
    {:else}{#each display as item, i (item.id)}{#if picked && expand === 'grapheme' && (i === 0 || visualGroup(display[i - 1]).id !== visualGroup(item).id)}<div class="visual-grid-heading">{groupLabel(visualGroup(item))}</div>{/if}{#if item.origin === 'corpus'}<button class="glyph-tile corpus" data-corpus={item.id} class:decided-checked={tileState(item) === 'checked'} class:decided-flagged={waiting(tileState(item))} onclick={() => inspect(item.id, null, display, updateItem, 'corpus')} aria-label={t('explore.tile.inspectCorpus', { label: shownLabel(item) })}><span class="tile-reading"><span class="tile-glyph" class:unassigned={isUnassigned(item)} lang={isUnassigned(item) ? undefined : 'ja'}>{shownLabel(item)}</span>{#if shownGrapheme(item)}<span class="tile-grapheme" lang="ja" title={t('chips.grapheme')}>{shownGrapheme(item)}</span>{/if}</span><span class="tile-details">{#each tileDetails(item) as line}<span>{line}</span>{/each}<span class="tile-id">{item.id}</span></span>{#if item.proxyable && item.image}<img class="glyph-image" src={item.image} alt={t('explore.tile.located', { label: shownLabel(item) })} loading={i < 24 ? "eager" : "lazy"} fetchpriority={i < 24 ? "high" : "auto"} decoding="async" />{:else}<span class="corpus-open"><b>{shownLabel(item)}</b><small>{t('character.image.unavailable')}</small></span>{/if}{#if tileState(item) === 'checked' || waiting(tileState(item))}<span class="tile-verdict" aria-hidden="true">{tileState(item) === 'checked' ? '✓' : '!'}</span>{/if}<span class="tile-footer">{#if tileState(item) === 'plain' || tileState(item) === 'withheld'}<span class="status-dot" class:withheld={tileState(item) === 'withheld'}></span>{/if}{#if productionLabel(item)}<span class="tile-production">{productionLabel(item)}</span>{/if}<span class="tile-number">{formatSerial(i + 1)}</span><span class="tile-arrow">↗</span></span></button>{:else}<button class="glyph-tile" data-unit={item.id} class:decided-checked={tileState(item) === 'checked'} class:decided-flagged={waiting(tileState(item))} onclick={() => inspect(item.id, null, display, updateItem)} aria-label={t('explore.tile.inspect', { label: shownLabel(item) })}><span class="tile-reading"><span class="tile-glyph" class:unassigned={isUnassigned(item)} lang={isUnassigned(item) ? undefined : 'ja'}>{shownLabel(item)}</span>{#if shownGrapheme(item)}<span class="tile-grapheme" lang="ja" title={t('chips.grapheme')}>{shownGrapheme(item)}</span>{/if}</span><span class="tile-details">{#each tileDetails(item) as line}<span>{line}</span>{/each}<span class="tile-id">{item.id}</span></span><Glyph {item} eager={i < 24} />{#if tileState(item) === 'checked' || waiting(tileState(item))}<span class="tile-verdict" aria-hidden="true">{tileState(item) === 'checked' ? '✓' : '!'}</span>{/if}<span class="tile-footer">{#if tileState(item) === 'plain' || tileState(item) === 'withheld'}<span class="status-dot" class:withheld={tileState(item) === 'withheld'}></span>{/if}{#if productionLabel(item)}<span class="tile-production">{productionLabel(item)}</span>{/if}<span class="tile-number">{formatSerial(i + 1)}</span><span class="tile-arrow">↗</span></span></button>{/if}{/each}{/if}
  </div>
  {#if !choosing && !loading && !display.length}<div class="empty"><span class="empty-mark">{picked || (settled && settled.total === 0) ? '∅' : flagged ? '✓' : '∅'}</span><h2>{picked && corpusFault ? t('explore.empty.samplesFailed', { char: picked.char }) : picked ? t('explore.empty.noOccurrenceOf', { char: picked.char }) : settled && settled.total === 0 ? t('explore.empty.noOccurrenceOfTerm', { term: readable }) : flagged ? t('explore.empty.nothingFlagged') : t('explore.empty.noCharacters')}</h2>{#if query}<button class="primary" onclick={clearQuery}>{t('explore.clearSearch')}</button>{:else}<a href={localize('/review')} class="primary">{t('explore.startRound')}</a>{/if}</div>{/if}
  {#if picked && (!visual && local.length < (data?.total ?? 0) || corpusOffset < corpusTotal) && !loading}
    <div class="load-more">
      {#if corpusOffset < corpusTotal}<button onclick={moreCorpus}>{t('explore.moreFromCorpus')}</button>{/if}
      {#if !visual && local.length < (data?.total ?? 0)}<button onclick={() => load(true)}>{t('explore.moreHere')}</button>{/if}
    </div>
  {:else if !choosing && !picked && data && items.length < data.total && !loading}
    <div class="load-more"><button onclick={() => { offset = items.length; load(true) }}>{t('explore.moreCharacters')}</button></div>
  {/if}
  <div class="collection-bottom">{#if localName()}<span lang={locale()}>{localName()}</span>{:else}<span lang="en">GLYPH ATLAS</span>{/if}<span>{t('explore.bottom.checked', { count: data?.counts.checked })} <span class="separator">·</span> {t('explore.bottom.flagged', { count: data?.counts.flagged })}</span></div>
</section>

<style>
  .visual-grid-heading{grid-column:1/-1;font-size:14px;padding:20px 2px 12px;color:var(--muted);background:var(--paper,#fafafa)}
  .tile-production{font-family:system-ui,sans-serif;font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tile-number{margin-left:auto}
  .tile-footer .tile-arrow{margin-left:0}
  .tile-footer .status-dot{flex-shrink:0}
  @media(max-width:700px){.tile-production{display:none}}
  .status-dot.withheld{background:transparent;box-shadow:inset 0 0 0 1px #9b9ba3}
  .glyph-tile.decided-checked{background:#eef4f0}
  .glyph-tile.decided-flagged{background:var(--wrong-light)}
  .glyph-tile.decided-checked:hover{background:#e0ece4}
  .glyph-tile.decided-flagged:hover{background:#fbe3e6}
  /* Drawn above the hover panel, so the outline stays whole while the details show. */
  .decided-checked::after,.decided-flagged::after{content:'';position:absolute;inset:0;z-index:3;border:2px solid #458665;pointer-events:none}
  .decided-flagged::after{border-color:var(--wrong)}
  .decided-checked .tile-reading,.decided-flagged .tile-reading{right:38px;overflow:hidden;white-space:nowrap}
  .tile-verdict{position:absolute;top:8px;right:8px;z-index:3;display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;color:#fff;font:600 13px/1 system-ui,sans-serif}
  .decided-checked .tile-verdict{background:#458665}
  .decided-flagged .tile-verdict{background:var(--wrong)}
  @media(max-width:700px){.tile-verdict{top:6px;right:6px;width:18px;height:18px;font-size:11px}.decided-checked .tile-reading,.decided-flagged .tile-reading{right:28px}}
  .explore-status{display:flex;align-items:center;flex-wrap:wrap;gap:8px 24px;padding:4px 0 14px}
  .explore-status .collection-meta{margin-left:auto;padding:0}
  .collection-progress-link{display:flex;align-items:center;flex-wrap:wrap;gap:12px 20px;border:0;border-radius:0;background:transparent;text-align:left;padding:0;color:var(--muted);font-size:12px}
  .collection-progress-link b{font-weight:500;color:var(--ink)}
</style>
