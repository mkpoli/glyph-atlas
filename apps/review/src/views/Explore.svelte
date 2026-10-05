<script>
  import { onMount, tick, untrack } from 'svelte'
  import { replaceState } from '$app/navigation'
  import { page } from '$app/state'
  import { productionLabel } from '../components/ProductionBadge.svelte'
  import { cropDetails, repairOf } from '../lib/cropDetails.js'
  import { tileDate } from '../lib/dating.js'
  import VisualGroups from '../components/VisualGroups.svelte'
  import StyleFilter from '../components/StyleFilter.svelte'
  import PeriodFilter from '../components/PeriodFilter.svelte'
  import { STYLE_GROUPS, styleRank } from '../lib/style.js'
  import { isUnassigned, writtenLabel, visualGroup, matchesVisualGroup, graphemeChar } from '../lib/identity.js'
  import Glyph from '../components/Glyph.svelte'
  import ScriptText from '../components/ScriptText.svelte'
  import ScriptLine from '../components/ScriptLine.svelte'
  import ImageStyleToggle from '../components/ImageStyleToggle.svelte'
  import CharacterSearch from '../components/CharacterSearch.svelte'
  import ImageSearch from '../components/ImageSearch.svelte'
  import CharacterChips from '../components/CharacterChips.svelte'
  import WorkFilter from '../components/WorkFilter.svelte'
  import GraphemeGrid from '../components/GraphemeGrid.svelte'
  import NgramGrid from '../components/NgramGrid.svelte'
  import { NGRAM_KINDS, ngramCounts } from '../lib/ngrams.js'
  import { runText } from '../lib/runs.js'
  import { collage, tileAspect, LAYOUT_KEY, storedLayout } from '../lib/collage.js'
  import RunCandidate from '../components/RunCandidate.svelte'
  import RunStrip from '../components/RunStrip.svelte'
  import { KEPT, frequent, runsWith } from '../lib/frequentRuns.js'
  import SiteLinks from '../components/SiteLinks.svelte'
  import { catalogue, character, request, randomSeed, number, formatSerial, stored, remember } from '../lib/client.js'
  import { character as layerCharacter, occurrences, candidates as layerCandidates, gallery as layerGallery, decades } from '../lib/layers.js'
  import { t, around, withText, localName, locale, localize, delocalize } from '../lib/i18n.svelte.js'
  import { characterAddress, collectionAddress, corpusScope, expandFor, scopeFor, styleCounts, unslug } from '../lib/gallery.js'
  import { useSession } from '../lib/session.svelte.js'
  import { roundAddress } from '../lib/reviewRounds.js'
  import BulkBar from '../components/BulkBar.svelte'
  // `initial` is the collection page the server rendered: the seed it shuffled with, the filters in the
  // address, the collection's rows, the corpus sample and the progress line; without rows the view loads
  // them itself, with the same filters.
  // `gallery` is a character's page instead (`lib/gallery.js`).
  // `addressed` is the collection and character pages, whose address this view keeps; the Flagged view
  // and the collection behind a crop's dialog leave theirs alone.
  // `shown` is the character on show, for the page's title; the view changes it in place.
  let { flagged = false, addressed = false, inspect, ink = 'original', onink = () => {}, initial = null, gallery = null, shown = $bindable() } = $props()
  const session = useSession()
  const asked = untrack(() => initial), first = asked?.result ? asked : null, opened = untrack(() => gallery)
  let data = $state(first?.result ?? (opened ? { query: opened.picked.char, total: opened.total, available: opened.available,
    categories: opened.summary?.categories ?? [], documents: opened.summary?.documents ?? [], counts: opened.summary?.counts ?? {} } : null))
  let items = $state(first?.result.items ?? []), error = $state(''), loading = $state(!first && !opened)
  let grapheme = $state(asked?.grapheme ?? ''), work = $state(asked?.work ?? ''), offset = $state(0), seed = $state(asked?.seed ?? randomSeed())
  let query = $state(asked?.q ?? opened?.picked.char ?? '')
  let choosing = $state(false), catalogueRequest = null
  // Search by image: open with the image the box was given, or none yet.
  let imaging = $state(null)
  let filter = $state(asked?.group ?? 'all'), requestId = 0, closed = false
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
  // The style group the gallery is narrowed to ('' for all), and each list's counts by style group.
  // `styled` is whether the server files crops by style at all.
  let style = $state(opened?.style ?? ''), styled = $state(opened?.styled ?? false)
  // A gallery placed by its books' dates (`year`, oldest first) instead of by style, and the years it is
  // narrowed to ('1600-1699', `undated`, or '' for all), with each decade's crops for the period filter.
  let dateOrder = $state(opened?.order ?? ''), yearRange = $state(opened?.years ?? ''), decadeCounts = $state(null)
  const dateParams = () => expand === 'variants' ? {} : { order: dateOrder || undefined, years: yearRange || undefined }
  let localStyles = $state(opened?.localStyles ?? null), corpusStyles = $state(opened?.corpusStyles ?? null)
  // Whether the corpus has answered this gallery's first page; a page the server rendered has it.
  let corpusAnswered = $state(Boolean(opened))
  $effect(() => { shown = picked?.code_point ? { char: picked.char, code_point: picked.code_point } : null })
  /**
   * The address says what is on show, so it can be shared and reloaded: a character's page with its
   * scope and visual group, or the collection with its search and filters. The view changes what it
   * shows in place and rewrites the address to match, so typing never leaves the page.
   */
  function showInAddress() {
    // While a crop is open the address is the crop's; the list's own entry keeps the list's.
    if (!addressed || page.state.inspect) return
    const [path, search = ''] = (picked?.code_point
      ? characterAddress(picked.code_point, { scope: scopeFor(expand, picked), visual, style, ...(expand === 'variants' ? {} : { order: dateOrder, years: yearRange }) })
      // Text still being chosen from the candidate list is not a search yet.
      : collectionAddress({ q: choosing ? '' : query.trim(), grapheme, work, group: filter })).split('?')
    const target = localize(path) + (search && '?' + search)
    // A shallow rewrite leaves `page.url` as it was loaded, so the address bar is what is compared.
    if (location.pathname + location.search !== target) replaceState(target, page.state)
  }
  // The unfiltered homepage shows the collection's own rows and then a bounded corpus sample. The
  // sample is normalised by the same adapter and deduplicated against what is already on the page.
  let sample = $state(first ? sampleRows(first.sample, first.result.items) : []), sampleFault = $state(first?.sample ? (first.sample.status === 'ok' ? null : first.sample.status) : null)
  let collection = $state(asked?.collection ?? null)
  async function readCollection() {
    try { collection = await request('/atlas/collection/status') } catch { /* retry on the next interval */ }
  }
  const categories = $derived((data?.categories ?? []).filter(c => !flagged || c.flagged || c.hard))
  /** A grapheme key (`U+4EEE`, or a label's own code points) as the text it names. */
  const charOf = key => /^U\+[0-9A-F]{4,6}( U\+[0-9A-F]{4,6})*$/i.test(key)
    ? key.split(' ').map(point => String.fromCodePoint(parseInt(point.slice(2), 16))).join('') : key
  // Whether the graphemes are listed from the most crops or from the fewest; the choice is remembered.
  let order = $state(stored('atlas.browseOrder', 'most') === 'fewest' ? 'fewest' : 'most')
  function orderBy(value) { order = value; remember('atlas.browseOrder', value) }
  // The corpus glyphs of every character, as [label, grapheme, glyphs], read once when the search box is
  // first focused; the Flagged view has no use for them. A service without them (the local review
  // service has none) or a failed read leaves the tiles counting crops for the rest of the visit.
  let corpusCounts = $state(null), corpusCountsAsked = false
  async function loadCorpusCounts() {
    if (flagged || corpusCountsAsked) return
    corpusCountsAsked = true
    try { const found = await request('/atlas/corpus/characters'); if (!closed) corpusCounts = found.items }
    catch { /* no corpus counts this visit */ }
  }
  // Tiles count corpus glyphs only while choosing one opens its gallery: with a work or a script group
  // chosen, a tile narrows the collection's listing instead, and corpus glyphs belong to neither.
  const countsCorpus = $derived(!flagged && !work && filter === 'all')
  // The characters the catalogue counts, gathered under their graphemes: 仮 and 假 are one tile. The
  // catalogue's counts are the whole collection's crops (`local`), whatever the filters; while
  // `countsCorpus`, a tile adds the corpus glyphs of its characters (`corpus`), so characters held only
  // as corpus glyphs are listed too.
  const graphemes = $derived.by(() => {
    const groups = new Map()
    const groupFor = key => { const group = groups.get(key) ?? { key, char: charOf(key), count: 0, local: 0, corpus: 0, members: [] }; groups.set(key, group); return group }
    for (const c of categories) {
      const group = groupFor(c.grapheme ?? c.label)
      const count = flagged ? c.flagged + c.hard : c.total
      group.members.push({ label: c.label, count }); group.count += count; group.local += count
    }
    if (countsCorpus) for (const [label, key, n] of corpusCounts ?? []) {
      const group = groupFor(key)
      const member = group.members.find(m => m.label === label)
      if (member) member.count += n; else group.members.push({ label, count: n })
      group.count += n; group.corpus += n
    }
    for (const group of groups.values()) group.members.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
    // Fewest first puts the graphemes the site holds fewest glyphs of at the top.
    const direction = order === 'fewest' ? -1 : 1
    return [...groups.values()].sort((a, b) => direction * (b.count - a.count) || a.key.localeCompare(b.key))
  })
  // Read only with a grapheme chosen: gathering the graphemes sorts thousands of them, and every page of
  // the collection brings their counts again.
  const chosenGrapheme = $derived(grapheme ? graphemes.find(group => group.key === grapheme) : undefined)
  // The browse panel counts single graphemes, pairs or trigrams; the choice is remembered. Runs are
  // counted for the work chosen, or the whole collection, and read when the panel first shows them.
  // A save can relabel part of a run, so it drops the counts and the panel reads them again.
  let unit = $state(stored('atlas.browseUnit', 'grapheme')), runs = $state(null), runsFailed = $state(false), runsFor = null, runsRequest = 0
  const counting = $derived(Object.hasOwn(NGRAM_KINDS, unit) && !flagged)
  function countBy(value) { unit = value; remember('atlas.browseUnit', value) }
  async function loadRuns() {
    const scope = work, kind = unit, id = ++runsRequest
    runsFor = kind + '|' + scope; runs = null; runsFailed = false
    try {
      const found = await ngramCounts(kind, scope)
      if (!closed && id === runsRequest) runs = found.items
    } catch { if (!closed && id === runsRequest) runsFailed = true }
  }
  function runsChanged() { if (counting) loadRuns(); else { runsFor = null; runsRequest += 1 } }
  $effect(() => { if (counting && unit + '|' + work !== runsFor) untrack(loadRuns) })
  /** The grapheme a corpus row is filed under, as the catalogue files a label. */
  const codesOf = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
  const rowGrapheme = row => row.grapheme?.code_point ?? row.grapheme ?? codesOf(row.label ?? '')
  const works = $derived((data?.documents ?? []).filter(w => !flagged || w.flagged || w.hard)
    .map(w => ({ ...w, count: flagged ? w.flagged + w.hard : w.total })))
  /** The label a tile shows. `identity.js` names an unassigned crop and a group without a label in
   * English, because it also runs outside the interface; the interface says it in the reader's language. */
  const shownLabel = item => isUnassigned(item) ? t('corpus.unassigned') : item.label
  // The grapheme beside the written form: always when the form is unassigned, else only when it differs.
  const shownGrapheme = item => { const g = graphemeChar(item); return g && (isUnassigned(item) || g !== item.label) ? g : null }
  const groupLabel = group => group.id === 'unassigned' ? t('corpus.unassigned')
    : group.label === 'Similar forms' ? t('explore.similarForms') : group.label
  /** Whether a crop still waits for a person: flagged, or hard to read after two reviewers skipped it. */
  const waiting = state => state === 'flagged' || state === 'hard'
  /** One state per tile: checked, flagged or hard get a corner badge and outline; withheld and plain a dot.
   * Needs fixing lists only crops waiting for a person, so there they carry no mark of it. */
  function tileState(item) {
    if (item.state === 'checked' || item.state === 'flagged' || item.state === 'hard') return item.state
    const kind = repairOf(item)?.kind
    return kind === 'verified' ? 'checked' : kind === 'withheld' ? 'withheld' : 'plain'
  }
  // The units the inspector may step through: the character's own records when a gallery is open, the
  // collection's rows otherwise. It is never empty on the ordinary homepage, so Next keeps working.
  const visibleLocal = $derived(local.filter(item => matchesVisualGroup(item, visual)))
  const editable = $derived((picked ? visibleLocal : items).filter(item => (item.origin ?? 'collection') === 'collection'))
  /** The same record from two services: an exact id match is the only dedup that is certain. */
  function sameInk(unit, other) {
    return Boolean(unit?.id) && (unit.id === other.id || unit.id === other.identity_key)
  }
  const corpusOnly = $derived(picked ? corpus.filter(other => !visibleLocal.some(unit => sameInk(unit, other))) : [])
  // With no style group chosen, a character's or grapheme's gallery reads as one list in style order:
  // running and cursive first, each group's own crops before its corpus glyphs. The two lists page
  // apart, each in that order, so a tile is shown once neither list can still bring one that goes
  // before it. A tile's place is its group's rank, doubled, plus one for the corpus; a list with
  // nothing more to bring holds nothing back. A variants widening lists each character in turn, in
  // style order within it, so its two lists are shown one after the other, as are those of a server
  // that files no crop by style.
  // Placed by date, the two lists merge the same way by their books' years, the undated last.
  const byYear = $derived(dateOrder === 'year' && expand !== 'variants')
  const merged = $derived(styled && !style && expand !== 'variants' && !byYear)
  const localDone = $derived(Boolean(visual) || local.length >= (data?.total ?? 0))
  // The corpus has said nothing until its first page arrives, and may yet bring running and cursive glyphs.
  const corpusDone = $derived(Boolean(corpusFault) || (corpusAnswered && corpusOffset >= corpusTotal))
  const localNext = $derived(localDone ? Infinity : 2 * (local.length ? styleRank(local.at(-1)) : 0))
  const corpusNext = $derived(corpusDone ? Infinity : 2 * (corpus.length ? styleRank(corpus.at(-1)) : 0) + 1)
  const yearKey = item => { const date = item.dating?.witness; return date ? date.start ?? date.end ?? Infinity : Infinity }
  const localNextYear = $derived(localDone ? Infinity : local.length ? yearKey(local.at(-1)) : -Infinity)
  const corpusNextYear = $derived(corpusDone ? Infinity : corpus.length ? yearKey(corpus.at(-1)) : -Infinity)
  // The counts by style group. Under a visual group the collection's own crops are narrowed here, in
  // the view, so theirs are counted from the tiles shown.
  const shownStyles = items => Object.fromEntries(STYLE_GROUPS.map((group, rank) => [group, items.filter(item => styleRank(item) === rank).length]))
  const styles = $derived(styleCounts(visual ? shownStyles(visibleLocal) : localStyles, corpusStyles))
  const galleryTiles = $derived.by(() => {
    if (byYear) {
      // A tile's place is its year and then its list, the collection's first: a corpus glyph of 1650 waits
      // while the collection may still bring one of 1650, and the undated wait the same way.
      const before = (a, b) => a[0] < b[0] || (a[0] === b[0] && a[1] <= b[1])
      // A list with nothing more to bring holds nothing back.
      const localNext = localDone ? [Infinity, 2] : [localNextYear, 0], corpusNext = corpusDone ? [Infinity, 2] : [corpusNextYear, 1]
      const upTo = before(localNext, corpusNext) ? localNext : corpusNext
      return [...visibleLocal.map(item => [yearKey(item), 0, item]), ...corpusOnly.map(item => [yearKey(item), 1, item])]
        .filter(place => before(place, upTo)).sort((a, b) => before(a, b) ? (before(b, a) ? 0 : -1) : 1).map(([, , item]) => item)
    }
    if (!merged) return [...visibleLocal, ...corpusOnly]
    const shownUpTo = Math.min(localNext, corpusNext)
    return [...visibleLocal.map(item => [2 * styleRank(item), item]), ...corpusOnly.map(item => [2 * styleRank(item) + 1, item])]
      .filter(([place]) => place <= shownUpTo).sort((a, b) => a[0] - b[0]).map(([, item]) => item)
  })
  // The collection's own crops are listed before the corpus sample: their labels are this atlas's own
  // judgement of the crop and are what a reviewer should reach first, while a corpus record's label is
  // the source's own transcription and is mostly right already.
  const homeCorpus = $derived(sample.filter(other => !items.some(unit => sameInk(unit, other))))
  const baseDisplay = $derived(choosing ? [] : picked ? galleryTiles : [...items, ...homeCorpus])
  function groupOrder(item) {
    const group = visualGroup(item)
    const index = (analysis?.groups ?? []).findIndex(entry => entry.id === group.id)
    // Crops in no group come first, under no heading, so none sits beneath another group's.
    return group.id === 'ungrouped' ? -1 : index >= 0 ? index : group.id === 'unassigned' ? Number.MAX_SAFE_INTEGER : 10000
  }
  // Group ids compare by code point: a locale collation would call た and タ equal.
  const byId = (a, b) => (a < b ? -1 : a > b ? 1 : 0)
  const display = $derived(picked && expand === 'grapheme'
    ? [...baseDisplay].sort((a, b) => groupOrder(a) - groupOrder(b) || byId(visualGroup(a).id, visualGroup(b).id)) : baseDisplay)
  // The grapheme view puts a heading over each shape group; crops in no group have none.
  const headed = item => picked && expand === 'grapheme' && visualGroup(item).id !== 'ungrouped'
  const headings = $derived(display.some(headed))
  // The next page arrives as the reader nears the end of the gallery. While there is one to come, the
  // grid of squares shows whole rows only: the crops of a part-filled last row wait for the page that
  // fills it. The collage has no rows to fill (`columns` is 0).
  let grid = $state(), sentinel = $state(), columns = $state(0), nearEnd = $state(false)
  // While the first page loads, the collage holds placeholders of crops' usual proportions.
  const skeleton = collage(Array.from({ length: 32 }, (_, i) => [1, 1.25, 0.85, 1.1, 1.4, 0.95, 1.2, 0.8][i % 8]))
  const hasMore = $derived(!choosing && (picked ? (!visual && local.length < (data?.total ?? 0)) || corpusOffset < corpusTotal
    : Boolean(data) && items.length < data.total))
  const whole = $derived(hasMore && columns && !headings ? Math.floor(display.length / columns) * columns : display.length)
  // Tiles join the page a slice at a time, each slice in a task of its own, so a page of crops
  // arriving never holds a slow machine up; the tiles the server rendered are there at once. The
  // collage is placed for every tile to come, so its blocks hold their height while the slices fill in.
  const SLICE = 12
  let reach = $state(untrack(() => display.length))
  const target = $derived(whole || display.length)
  $effect(() => {
    if (reach >= target) return
    const timer = setTimeout(() => { reach = Math.min(target, reach + SLICE) }, 0)
    return () => clearTimeout(timer)
  })
  const tiles = $derived(display.slice(0, Math.min(target, reach)))
  // The gallery is a collage by default (`lib/collage.js`), or a grid of squares; the choice is the
  // reader's, remembered, and set on the document before the first paint (`app.html`), so the page
  // the server rendered, which holds both, opens in it.
  let layout = $state('collage')
  function layOut(value) {
    layout = value; remember(LAYOUT_KEY, value)
    document.documentElement.dataset.gallery = value
  }
  // Each shape group of the grapheme view is a block of its own under its heading; any other
  // gallery is one block. A tile's number counts through the whole gallery.
  const sections = $derived.by(() => {
    const parts = []
    display.slice(0, target).forEach((item, i) => {
      const group = headed(item) ? visualGroup(item) : null, key = group?.id ?? ''
      if (!parts.length || parts.at(-1).key !== key) parts.push({ key, heading: group ? groupLabel(group) : '', items: [] })
      parts.at(-1).items.push([item, i])
    })
    return parts.map(part => {
      const placed = collage(part.items.map(([item]) => tileAspect(item)))
      return { key: part.key || 'all', heading: part.heading, block: placed.block, start: part.items[0][1], tiles: part.items.map(([item, i], at) => [item, i, placed.tiles[at]]) }
    })
  })
  function more() {
    // The list whose next page goes first; with a style group chosen, the collection's own crops first.
    if (picked) { if (!localDone && (byYear ? corpusDone || localNextYear <= corpusNextYear : !merged || localNext < corpusNext)) load(true); else moreCorpus() }
    else { offset = items.length; load(true) }
  }
  $effect(() => { if (nearEnd && hasMore && !loading && !error && reach >= target) untrack(more) })
  $effect(() => {
    if (!grid) return
    void layout
    // The collage has no rows to fill; a grid's columns are counted from its template.
    const measure = () => { columns = document.documentElement.dataset.gallery === 'grid' ? getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length : 0 }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(grid)
    return () => observer.disconnect()
  })
  $effect(() => {
    if (!sentinel) return
    const observer = new IntersectionObserver(([entry]) => { nearEnd = entry.isIntersecting }, { rootMargin: '0px 0px 1200px 0px' })
    observer.observe(sentinel)
    return () => observer.disconnect()
  })
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
    // A further page goes before the corpus glyphs shown after the collection's own crops, so the tiles
    // from the end of those crops on are drawn again, a slice at a time.
    if (!append) { showInAddress(); clearSelection() }
    const id = ++requestId; loading = true; error = ''
    try {
      if (picked) {
        if (!append) { local = []; corpus = []; corpusTotal = 0; corpusOffset = 0; localStyles = null; corpusStyles = null; corpusAnswered = false }
        // Local records page by their own count, and they are shown before the corpus is asked:
        // a corpus that cannot answer must not hide the records this collection does hold.
        const found = await occurrences(picked.code_point, { expand, limit: 60, offset: append ? local.length : 0, style: style || undefined, ...dateParams() })
        if (closed || id !== requestId) return
        const rows = found.items.map(item => ({ ...item, origin: 'collection' }))
        // A new list is drawn from its start; a further page goes before the corpus glyphs shown after
        // the collection's own crops, so the tiles from the end of those crops on are drawn again.
        reach = append ? Math.min(reach, local.length) : 0
        local = append ? [...local, ...rows] : rows
        localStyles = found.styles ?? null; styled = Boolean(found.style_groups)
        data = { ...(data ?? {}), query: picked.char, total: found.counts.total, available: found.counts.exact_total,
                 categories: data?.categories ?? [], documents: data?.documents ?? [], counts: data?.counts ?? {} }
        // The chips come from the card, and the card says which widening is in force: refetch it so a
        // chip that was just switched on reads as on.
        // A variants widening changes no field of the card, and a further page none at all.
        if (!append && expand !== 'variants') layerCharacter(picked.code_point, expand)
          .then(card => { if (!closed && id === requestId) picked = { ...picked, ...card } })
          .catch(() => {})
        if (append) return
        loadDecades(picked.code_point, expand)
        // The corpus leads are fetched whole, because their service pins located anchors first and a
        // page of text hits would bury them.
        corpusFault = null
        try {
          const leads = await layerCandidates(picked.code_point, 60, 0,
            { scope: corpusScope(expand), visual_group: visual || undefined, style: style || undefined, ...dateParams() })
          if (closed || id !== requestId) return
          corpusStyles = leads.styles ?? null; corpusAnswered = true
          corpus = (leads.glyph_items ?? []).map(item => ({ ...item, label: writtenLabel(item), origin: 'corpus' }))
          corpusTotal = leads.glyphs ?? 0; corpusOffset = corpus.length
          analysis = leads.visual_analysis ?? picked.visual_analysis ?? null
          familyTotal = leads.family_total ?? null; unassignedCount = leads.unassigned_count ?? null
        } catch (e) {
          if (!closed && id === requestId) { corpusFault = e.status === 502 ? 'error' : 'not-loaded'; corpusStyles = {} }
        }
        return
      }
      catalogueRequest = new AbortController()
      // The corpus half is requested alongside the crops, and deduplicated against them once both are in.
      const sampled = append ? null : requestSample()
      const result = await catalogue({ grapheme, document: work, q: query, group: filter, state: flagged ? 'attention' : 'all',
        reported: flagged ? (showReported ? 'show' : 'hide') : null, seed, offset, limit: 60 }, { signal: catalogueRequest.signal, priority: 'low' })
      if (closed || id !== requestId) return
      if (!append && openEmptyGrapheme(result)) return
      // A further page keeps the counts by character and work it came with: they answer the same filters,
      // and replacing them redraws the work menu and regathers thousands of graphemes for every page.
      data = append && data ? { ...result, categories: data.categories, documents: data.documents } : result
      reach = append ? Math.min(reach, items.length) : 0
      items = append ? [...items, ...result.items] : result.items
      if (!append) await showSample(id, sampled, result.items)
    } catch (e) { if (!closed && id === requestId && e.name !== 'AbortError') error = e.message }
    finally { if (!closed && id === requestId) loading = false }
  }
  // A gallery the server rendered reads its decades once it is in the browser.
  $effect(() => { const code = picked?.code_point, scope = expand; if (code) untrack(() => loadDecades(code, scope)) })
  // The decades of the gallery on show, for the period filter; they depend on the character and scope only.
  let decadesFor = ''
  function loadDecades(code, scope) {
    const key = `${code} ${scope}`
    if (scope === 'variants') { decadeCounts = null; decadesFor = ''; return }
    if (key === decadesFor) return
    decadesFor = key; decadeCounts = null
    decades(code, { scope: scope === 'grapheme' ? 'grapheme' : 'character' })
      .then(found => { if (!closed && decadesFor === key) decadeCounts = [...found.local, ...found.corpus] })
      .catch(() => { if (decadesFor === key) decadesFor = '' })
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

  // Runs are shown with the gallery without being asked for: the collection's most frequent pairs or
  // trigrams on the bare collection page, and those a picked character is part of on its gallery. The
  // server renders the first list of each; another is read when the view moves to it.
  const bare = $derived(!flagged && !picked && !choosing && !query.trim() && !grapheme && !work && filter === 'all')
  let runKind = $state('pair'), homeRuns = $state(asked?.runs ? { kind: 'pair', items: asked.runs } : null), homeRunsFailed = $state(false)
  async function loadHomeRuns(kind) {
    homeRunsFailed = false
    try { const items = (await frequent(kind)).slice(0, KEPT); if (!closed && runKind === kind) homeRuns = { kind, items } }
    catch { if (!closed && runKind === kind) homeRunsFailed = true }
  }
  $effect(() => { if (bare && homeRuns?.kind !== runKind) untrack(() => loadHomeRuns(runKind)) })
  // A character's runs show once they are here, and not at all when it has none among the counted
  // ones or they cannot be read: the strip is extra to the gallery, which loads below it meanwhile.
  let charRuns = $state(opened?.runs ? { char: opened.picked.char, items: opened.runs } : null), charRunsFor = untrack(() => charRuns?.char ?? '')
  async function loadCharRuns(char) {
    try { const items = await runsWith(char); if (!closed && picked?.char === char) charRuns = { char, items } }
    catch { /* no strip for this character */ }
  }
  $effect(() => { const char = picked?.char; if (char && char !== charRunsFor) { charRunsFor = char; untrack(() => loadCharRuns(char)) } })
  const runWords = { pair: () => t('explore.pairs'), trigram: () => t('explore.trigrams') }
  const runFailed = kind => kind === 'trigram' ? t('explore.trigrams.failed') : t('explore.pairs.failed')

  // A query of two to eight characters is also a run, which the candidate list offers (`RunCandidate`).
  const run = $derived(flagged ? '' : runText(query))

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
        { scope: corpusScope(expand), visual_group: visual || undefined, style: style || undefined, ...dateParams() })
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
  // `scope` is an address's: `exact`, `family` or `variants`; without one the card's default applies.
  async function pick(item, scope = null) {
    let expansionWillLoad = false
    choosing = false
    clearTimeout(searchTimer)
    requestId += 1
    const current = ++pickId
    picked = null
    // A year range is the last character's; another may have nothing in it.
    yearRange = ''
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
        const nextExpand = expandFor(scope, card)
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
    if (expand === 'variants') return !isUnassigned(row)
      && [picked.char, ...(picked.variants?.items ?? []).map(v => v.char)].includes(writtenLabel(row))
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
      runsChanged()
      const summary = await catalogue({ grapheme, document: work, q: query, group: filter, state: flagged ? 'attention' : 'all',
        reported: flagged ? (showReported ? 'show' : 'hide') : null, limit: 1 })
      if (!closed) data = { ...data, counts: summary.counts, categories: summary.categories, documents: summary.documents, total: summary.total, available: summary.available, reported_count: summary.reported_count }
    } catch (e) { if (!closed) error = e.message }
  }
  // Several tiles can be given one written character at once, as when the same misreading repeats.
  // Ctrl/⌘-click or the corner box toggles a tile, Shift-click takes the range from the last one, X
  // toggles the focused tile, and once anything is selected a mouse click toggles too; Enter still opens
  // the inspector, so a keyboard reader can check a tile before marking it. Esc clears.
  let selected = $state(new Set()), anchor = null, bulkTarget = $state(''), bulkBusy = $state(false), bulkError = $state('')
  let bulkDone = $state(null)
  const BATCH = 144
  /** A corpus glyph whose image this site may not show cannot be judged from the grid. */
  const selectable = item => item.origin !== 'corpus' || Boolean(item.proxyable && item.image)
  function tileClick(event, item, index, open) {
    const toggling = event.target.closest?.('.tile-select') || event.ctrlKey || event.metaKey || event.shiftKey || (selected.size && event.detail > 0)
    if (!toggling || !selectable(item)) { open(); return }
    event.preventDefault()
    const next = new Set(selected)
    if (event.shiftKey && anchor != null) {
      const [from, to] = anchor < index ? [anchor, index] : [index, anchor]
      for (const tile of tiles.slice(from, to + 1)) if (selectable(tile)) next.add(tile.id)
    } else if (next.has(item.id)) next.delete(item.id)
    else next.add(item.id)
    anchor = index; selected = next; bulkError = ''
  }
  function clearSelection() { selected = new Set(); anchor = null; bulkError = '' }
  function selectShown() { selected = new Set(tiles.filter(selectable).map(tile => tile.id)); bulkError = '' }
  function selectionKeys(event) {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.target.closest?.('input, textarea, dialog')) return
    if (event.key === 'Escape' && selected.size) { clearSelection(); return }
    const tile = event.target.closest?.('.glyph-grid .glyph-tile')
    if (!tile || (event.key !== 'x' && event.key !== 'X')) return
    const index = tiles.findIndex(item => item.id === (tile.dataset.unit ?? tile.dataset.corpus))
    if (index >= 0) { event.preventDefault(); tileClick({ ctrlKey: true, detail: 0, target: event.target, preventDefault() {} }, tiles[index], index, () => {}) }
  }
  const writtenAs = item => item.written_character || item.label
  /** A tile after a correction, or null when the correction takes it out of what this view shows. */
  function corrected(item, result) {
    const next = { ...item, label: bulkTarget, char: bulkTarget, written_character: bulkTarget, identity_status: 'assigned',
      state: result?.state ?? 'checked', revision: result?.revision ?? item.revision + 1 }
    return (flagged && !waiting(next.state)) || !fitsGallery(next) ? null : next
  }
  function patch(change) {
    const apply = list => list.flatMap(item => (item.id in change ? (change[item.id] ? [change[item.id]] : []) : [item]))
    items = apply(items); local = apply(local); corpus = apply(corpus); sample = apply(sample)
  }
  /** Tiles the site refused, read again so the grid shows what they are now. */
  async function refresh(ids) {
    const change = {}
    await Promise.all(ids.map(async id => {
      const tile = display.find(item => item.id === id)
      try {
        const now = tile?.origin === 'corpus' ? { ...tile, ...(await request('/atlas/corpus/character?id=' + encodeURIComponent(id))) } : await character(id)
        change[id] = (flagged && !waiting(now.state)) || !fitsGallery(now) ? null : now
      } catch { change[id] = null }
    }))
    patch(change)
  }
  async function applyBulk() {
    const target = bulkTarget
    if (!target || bulkBusy) return
    const chosen = display.filter(item => selected.has(item.id) && selectable(item))
    // A tile a person already checked as the character has nothing to change; every other one is sent,
    // and one already written as the character is confirmed.
    const crops = chosen.filter(item => !(writtenAs(item) === target && item.state === 'checked'))
    let kept = chosen.length - crops.length
    bulkBusy = true; bulkError = ''
    const saved = [], before = {}, after = {}
    let refused = []
    try {
      for (let at = 0; at < crops.length; at += BATCH) {
        const part = crops.slice(at, at + BATCH), id = crypto.randomUUID()
        const result = await request('/atlas/corrections', { id, character: target,
          crops: part.map(item => ({ id: item.id, revision: item.revision,
            ...(item.origin === 'corpus' ? { source_revision: item.source_revision } : { image_sha256: item.image_sha256 }) })) })
        saved.push(id)
        const byId = Object.fromEntries((result.results ?? []).map(r => [r.target_id, r]))
        kept += (result.unchanged ?? []).length
        for (const item of part) if (byId[item.id]) { before[item.id] = item; after[item.id] = corrected(item, byId[item.id]) }
      }
    } catch (e) {
      refused = (e.targets ?? []).map(target => target.id)
      bulkError = refused.length ? t('bulk.refused', { count: refused.length }) : e.status === 429 ? t('bulk.tooMany') : e.status === 409 ? t('bulk.changed') : e.message
    } finally {
      bulkBusy = false
    }
    // Crops in a saved batch, tiles with nothing to change, and refused crops are done with.
    const handled = new Set([...crops.slice(0, saved.length * BATCH), ...chosen.filter(item => !crops.includes(item))].map(item => item.id).concat(refused))
    if (saved.length) {
      patch(after)
      bulkDone = { batches: saved, count: Object.keys(after).length, char: target, before, kept }
      runsChanged()
    }
    // What was saved or refused leaves the selection; what a later batch never reached stays selected.
    selected = new Set([...selected].filter(id => !handled.has(id))); anchor = null
    if (refused.length) await refresh(refused)
  }
  async function undoBulk() {
    const done = bulkDone
    if (!done || bulkBusy) return
    bulkBusy = true; bulkError = ''
    try {
      for (const id of [...done.batches].reverse()) await request(`/atlas/corrections/${id}/undo`, {})
      bulkDone = null
      const shown = new Set([...items, ...local, ...corpus, ...sample].map(item => item.id))
      if (Object.keys(done.before).every(id => shown.has(id))) patch(done.before)
      else await load()
      runsChanged()
    } catch (e) { bulkError = e.message }
    finally { bulkBusy = false }
  }
  function select(value) { grapheme = value; offset = 0; load() }
  // A tile that counts corpus glyphs, which the collection's own listing leaves out, opens its
  // grapheme's gallery, which lists both; any other narrows the listing, keeping its filters.
  function openGrapheme(key) {
    if (key && countsCorpus && graphemeByKey.get(key)?.corpus > 0) pick({ code_point: key, char: charOf(key) }, 'family')
    else select(key)
  }
  // An address that narrows the collection to a grapheme it holds no crop of (one held only as corpus
  // glyphs) opens the grapheme's gallery instead of an empty listing.
  function openEmptyGrapheme(result) {
    if (!grapheme || work || flagged || query || filter !== 'all' || result.total > 0) return false
    pick({ code_point: grapheme, char: charOf(grapheme) }, 'family')
    return true
  }
  // The graphemes the browser lists, by key: a query's candidate shows in its grapheme's card when the
  // collection holds that very character, as the browser's card lists it.
  const graphemeByKey = $derived(new Map(graphemes.map(group => [group.key, group])))
  function groupOf(item) {
    const group = graphemeByKey.get(item.grapheme?.code_point ?? codesOf(item.char ?? ''))
    return group && group.members.some(member => member.label === item.char) ? group : null
  }
  /** "All forms" in a query's card: the collection narrowed to that grapheme, as in the browser. */
  function chooseGrapheme(key) {
    clearTimeout(searchTimer); pickId += 1; requestId += 1; choosing = false
    visual = ''; analysis = null; familyTotal = null; unassignedCount = null
    query = ''; picked = null; expand = 'none'; local = []; corpus = []; corpusTotal = 0; corpusOffset = 0
    openGrapheme(key)
  }
  function shuffle() { seed = randomSeed(); offset = 0; load() }
  /**
   * Back or Forward to an address this view rewrote loads the page as it was first loaded, while the
   * address bar keeps what the view had moved on to. The view follows the address bar.
   */
  function followAddress() {
    const { path } = delocalize(location.pathname)
    const code = path.startsWith('/character/') ? unslug(path.slice(11)) : null
    const scope = new URLSearchParams(location.search).get('scope')
    if (code && code !== picked?.code_point) pick({ code_point: code }, scope)
    // Back or Forward between two scopes of the same character: the widening the address names.
    else if (code && picked && expandFor(scope, picked) !== expand) expand = expandFor(scope, picked)
    else if (!code && path === '/') {
      const wanted = new URLSearchParams(location.search)
      const q = wanted.get('q') ?? '', g = wanted.get('grapheme') ?? '', w = wanted.get('work') ?? '', group = wanted.get('group') ?? 'all'
      if (!picked && q === query.trim() && g === grapheme && w === work && group === filter) return
      clearTimeout(searchTimer); pickId += 1; choosing = false
      visual = ''; analysis = null; familyTotal = null; unassignedCount = null
      picked = null; expand = 'none'; local = []; corpus = []; corpusTotal = 0; corpusOffset = 0; offset = 0
      query = q; grapheme = g; work = w; filter = group; load()
    }
  }
  onMount(() => { layout = storedLayout() })
  onMount(() => { const redirected = first ? openEmptyGrapheme(first.result) : false; if (!collection) readCollection(); if (!first && !opened) load(); if (addressed && !redirected) followAddress(); const timer = setInterval(readCollection, 30000); return () => { closed = true; clearInterval(timer); clearTimeout(searchTimer); catalogueRequest?.abort() } })
  // Widening is the reader's choice and only it reloads the gallery; picking a character resets the
  // widening itself and loads once through `pick`.
  // The first run is the widening the page opened with, already loaded.
  let shownExpand = untrack(() => expand)
  $effect(() => { const value = expand; if (value === shownExpand) return; shownExpand = value; untrack(() => { visual = ''; if (picked && !closed) load() }) })
</script>

<svelte:window onkeydown={selectionKeys} />

<!-- A crop's label in its script's colour; an unassigned crop says so instead. -->
{#snippet tileLabel(item)}{#if isUnassigned(item)}{shownLabel(item)}{:else}<ScriptText text={item.label} />{/if}{/snippet}
<!-- One crop of the gallery, placed in the collage by `style` (`lib/collage.js`). -->
{#snippet tile(item, i, style)}{#if item.origin === 'corpus'}<button class="glyph-tile corpus collage-cell" {style} data-corpus={item.id} class:decided-checked={tileState(item) === 'checked'} class:decided-flagged={!flagged && waiting(tileState(item))} class:selected={selected.has(item.id)} onclick={event => tileClick(event, item, i, () => inspect(item.id, null, display, updateItem, 'corpus'))} aria-label={t('explore.tile.inspectCorpus', { label: shownLabel(item) }) + (selected.has(item.id) ? t('bulk.tileSelected') : '')}>{#if selectable(item)}<span class="tile-select" aria-hidden="true" title={t('bulk.select')}>{selected.has(item.id) ? '✓' : ''}</span>{/if}<span class="tile-label"><span class="tile-glyph" class:unassigned={isUnassigned(item)}>{@render tileLabel(item)}</span>{#if shownGrapheme(item)}<span class="tile-grapheme" title={t('chips.grapheme')}><ScriptText text={shownGrapheme(item)} titled={false} /></span>{/if}</span><span class="tile-details">{#each cropDetails(item) as line}<span><ScriptLine {line} /></span>{/each}<span class="tile-id">{item.id}</span></span>{#if item.proxyable && item.image}<Glyph {item} alt={t('explore.tile.located', { label: shownLabel(item) })} eager={i < 24} />{:else}<span class="corpus-open"><b>{@render tileLabel(item)}</b><small>{t('character.image.unavailable')}</small></span>{/if}{#if tileState(item) === 'checked' || !flagged && waiting(tileState(item))}<span class="tile-verdict" aria-hidden="true">{tileState(item) === 'checked' ? '✓' : '!'}</span>{/if}<span class="tile-footer">{#if tileState(item) === 'plain' || tileState(item) === 'withheld'}<span class="status-dot" class:withheld={tileState(item) === 'withheld'}></span>{/if}{#if tileDate(item)}<span class="tile-year">{tileDate(item)}</span>{/if}{#if productionLabel(item)}<span class="tile-production">{productionLabel(item)}</span>{/if}<span class="tile-number">{formatSerial(i + 1)}</span><span class="tile-arrow">↗</span></span></button>{:else}<button class="glyph-tile collage-cell" {style} data-unit={item.id} class:decided-checked={tileState(item) === 'checked'} class:decided-flagged={!flagged && waiting(tileState(item))} class:selected={selected.has(item.id)} onclick={event => tileClick(event, item, i, () => inspect(item.id, null, display, updateItem))} aria-label={t('explore.tile.inspect', { label: shownLabel(item) }) + (selected.has(item.id) ? t('bulk.tileSelected') : '')}><span class="tile-select" aria-hidden="true" title={t('bulk.select')}>{selected.has(item.id) ? '✓' : ''}</span><span class="tile-label"><span class="tile-glyph" class:unassigned={isUnassigned(item)}>{@render tileLabel(item)}</span>{#if shownGrapheme(item)}<span class="tile-grapheme" title={t('chips.grapheme')}><ScriptText text={shownGrapheme(item)} titled={false} /></span>{/if}</span><span class="tile-details">{#each cropDetails(item) as line}<span><ScriptLine {line} /></span>{/each}<span class="tile-id">{item.id}</span></span><Glyph {item} eager={i < 24} />{#if tileState(item) === 'checked' || !flagged && waiting(tileState(item))}<span class="tile-verdict" aria-hidden="true">{tileState(item) === 'checked' ? '✓' : '!'}</span>{/if}<span class="tile-footer">{#if tileState(item) === 'plain' || tileState(item) === 'withheld'}<span class="status-dot" class:withheld={tileState(item) === 'withheld'}></span>{/if}{#if tileDate(item)}<span class="tile-year">{tileDate(item)}</span>{/if}{#if productionLabel(item)}<span class="tile-production">{productionLabel(item)}</span>{/if}<span class="tile-number">{formatSerial(i + 1)}</span><span class="tile-arrow">↗</span></span></button>{/if}{/snippet}
<!-- The search term in its scripts' colours, and the code points `readable` adds to it. -->
{#snippet term()}<ScriptText text={query} />{readable.slice(query.length)}{/snippet}

<section class="explore">
  <div class="page-status">
    <h1 class="visually-hidden">{flagged ? t('explore.heading.flagged') : t('explore.heading.atlas')}</h1>
    <SiteLinks />
    <div class="collection-meta"><span class="live-dot"></span>{#if picked}<span>{t('explore.meta.glyphs', { count: display.length })}</span><span class="meta-divider">/</span><span>{expand === "grapheme" ? t('explore.meta.characters', { count: picked.grapheme?.character_count ?? 1 }) : expand === "variants" ? t('explore.meta.characters', { count: 1 + (picked.variants?.items?.length ?? 0) }) : t('explore.meta.characters', { count: 1 })}</span>{:else if !flagged && collection?.archive}<span>{t('explore.meta.indexedCrops', { count: collection.archive.character_crops })}</span>{#if data?.counts}<span class="meta-divider">/</span><span>{t('explore.meta.reviewedCrops', { count: (data.counts.checked ?? 0) + (data.counts.flagged ?? 0) })}</span>{/if}{:else}<span>{t('explore.meta.glyphsTotal', { count: flagged ? (data?.total ?? 0) + sample.length : data?.available })}</span><span class="meta-divider">/</span><span>{t('explore.meta.graphemes', { count: graphemes.filter(group => group.local > 0).length })}</span>{/if}</div>
  </div>
  <div class="collection-toolbar" onfocusin={e => { if (e.target.closest('.character-search') && e.target.matches('input')) loadCorpusCounts() }}>
    <!-- The box, empty and focused, lists the collection's graphemes; one chosen narrows the grid, and a
         form in a tile's popover opens that form's own gallery. -->
    {#snippet browse(close)}
      {#if flagged}<p class="candidate-status">{t('explore.graphemes')}</p>
      {:else}<div class="browse-unit" role="group" aria-label={t('explore.browseUnit')}>
        {#each [['grapheme', () => t('explore.graphemes')], ['pair', () => t('explore.pairs')], ['trigram', () => t('explore.trigrams')]] as [value, text]}<button type="button" aria-pressed={unit === value} onclick={() => countBy(value)}>{text()}</button>{/each}
      </div>{/if}
      {#if !counting}<div class="browse-unit" role="group" aria-label={t('explore.browseOrder')}>
        {#each [['most', () => t('explore.order.most')], ['fewest', () => t('explore.order.fewest')]] as [value, text]}<button type="button" aria-pressed={order === value} onclick={() => orderBy(value)}>{text()}</button>{/each}
      </div>{/if}
      <!-- A grid draws a slice at a time; another order or kind starts it again from the first. -->
      {#if counting}{#key unit + '|' + work}<NgramGrid kind={unit} {runs} failed={runsFailed} onretry={loadRuns} {work} />{/key}
      {:else}{#key order}<GraphemeGrid groups={graphemes} value={grapheme} onchoose={key => { close(); openGrapheme(key) }}
                    onform={form => { close(); pick({ code_point: codesOf(form), char: form }, 'exact') }} />{/key}{/if}
    {/snippet}
    {#snippet runLead()}{#key run}<RunCandidate text={run} />{/key}{/snippet}
    <CharacterSearch bind:value={query} oninput={seek} onselect={pick} {browse} {groupOf} onchoosegroup={chooseGrapheme} lead={run ? runLead : null}
                     onform={form => pick({ code_point: codesOf(form), char: form }, true)}
                     token={grapheme ? charOf(grapheme) : ''} tokenLabel={t('explore.clearGrapheme', { grapheme: charOf(grapheme) })} ontokenclear={() => select('')}
                     onsubmit={() => { clearTimeout(searchTimer); offset = 0; submitQuery() }}
                     onimage={flagged ? null : file => { if (file || !imaging) imaging = { file, key: (imaging?.key ?? 0) + 1 } }} />
    <div class="filter-tabs" aria-label={t('explore.filter.label')}>{#each [['all', () => t('explore.filter.all')], ['kana', () => t('explore.filter.kana')], ['kanji', () => t('explore.filter.kanji')], ['hangul', () => t('explore.filter.hangul')], ['gugyeol', () => t('explore.filter.gugyeol')]] as [value, text]}<button class:active={filter === value} onclick={() => { filter = value; offset = 0; load() }}>{text()}</button>{/each}</div>
    <!-- A work narrows the collection's listing; choosing one leaves a picked character's gallery. -->
    <WorkFilter {works} value={work} onchange={value => { work = value; if (picked || query) clearQuery(); else { offset = 0; load() } }} />
    <span class="toolbar-space"></span>
    <ImageStyleToggle {ink} onchange={onink} />
    <div class="layout-toggle" role="group" aria-label={t('explore.layout.label')}>
      {#each [['collage', () => t('explore.layout.collage')], ['grid', () => t('explore.layout.grid')]] as [value, text]}<button class:active={layout === value} aria-pressed={layout === value} onclick={() => layOut(value)}>{text()}</button>{/each}
    </div>
    <!-- A round asks about one written character, so it is offered only for a grapheme of one form. -->
    {#if chosenGrapheme && !flagged}<a class="quiet-link" href={localize(roundAddress(chosenGrapheme.key))}><ScriptLine line={withText('explore.reviewGrapheme', 'grapheme', { grapheme: chosenGrapheme.char })} /></a>{/if}
    {#if flagged && data?.reported_count}<button class="quiet-link" onclick={toggleReported}>{showReported ? t('explore.flagged.hideReported') : t('explore.flagged.showReported', { count: data.reported_count })}</button>{/if}
    <button class="shuffle" onclick={shuffle} disabled={loading} aria-label={t('explore.shuffle.aria')}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 6h3c4 0 8 12 12 12h3M17 14l4 4-4 4M3 18h3c1.7 0 3.5-2.3 5-5M14 8c1.5-1.4 2.6-2 4-2h3M17 2l4 4-4 4"/></svg>{t('explore.shuffle')}</button>
  </div>
  {#if imaging}<ImageSearch given={imaging} onclose={() => imaging = null} {inspect} />{/if}
  {#if error}<div class="error-message" role="alert">{error}<button onclick={() => load()}>{t('common.retry')}</button></div>{/if}
  {#if picked}
    <CharacterChips card={picked} bind:expand onselect={item => pick({ code_point: item }, 'exact')} />
    {#if charRuns?.char === picked.char && charRuns.items.length}<RunStrip runs={charRuns.items} heading={t('explore.runs.with', { char: picked.char })} />{/if}
    {#if styled || style}<StyleFilter counts={styles} value={style} onchange={value => { style = value; load() }} />{/if}
    {#if picked && expand !== 'variants'}<PeriodFilter counts={decadeCounts} value={yearRange} order={dateOrder} onchange={value => { yearRange = value; load() }} onorder={value => { dateOrder = value; load() }} />{/if}
    {#if expand === 'grapheme'}<VisualGroups {analysis} count={familyTotal} unassigned={unassignedCount} value={visual} onchange={value => { visual = value; load() }} />{/if}
    <p class="find-count" role="status">
      {t('explore.meta.glyphs', { count: display.length })}
      {#if editable.length}<span class="separator">·</span> {t('explore.count.here', { count: editable.length })}{/if}
      {#if corpusOnly.length}<span class="separator">·</span> {t('explore.count.fromCorpus', { count: corpusOnly.length })}{/if}
      {#if corpusFault}<span class="separator">·</span> <span class="corpus-fault" role="status">{t('explore.samplesUnavailable')}</span> <button class="quiet-link" onclick={() => load()}>{t('common.retry')}</button>{/if}
      {#if loading}<span class="find-pending"> …</span>{/if}
    </p>
  {/if}
  {#if bare}<RunStrip runs={homeRuns?.kind === runKind ? homeRuns.items : null} heading={t('explore.runs.heading')} failed={homeRunsFailed ? runFailed(runKind) : ''} onretry={() => loadHomeRuns(runKind)}
    tabs={{ value: runKind, options: Object.entries(runWords), onchange: value => { runKind = value; homeRunsFailed = false } }} />{/if}
  {#if picked}
  {:else if !query && (items.length || homeCorpus.length || sampleFault)}
    <p class="find-count" role="status">{#if items.length}{t('explore.count.here', { count: items.length })}{/if}{#if homeCorpus.length}{#if items.length}<span class="separator">·</span> {/if}{t('explore.count.fromCorpus', { count: homeCorpus.length })}{/if}{#if sampleFault}{#if items.length || homeCorpus.length}<span class="separator">·</span> {/if}<span class="corpus-fault" role="status">{sampleFault === 'error' ? t('explore.corpus.error') : t('explore.corpus.notLoaded')}</span>{/if}{#if loading}<span class="find-pending"> …</span>{/if}</p>
  {:else if query && settled}
    <p class="find-count" role="status">{around('explore.occurrencesOf', 'character', { count: settled.total })[0]}<b>{@render term()}</b>{around('explore.occurrencesOf', 'character', { count: settled.total })[1]}{#if loading}<span class="find-pending"> …</span>{/if}</p>
  {/if}
  <div class="glyph-grid collage" class:selecting={selected.size > 0} bind:this={grid} aria-label={flagged ? t('explore.heading.flagged') : t('explore.grid.collection')} aria-busy={loading}>
    {#if loading && !display.length}<div class="collage-block" style={skeleton.block}>{#each skeleton.tiles as style}<div class="glyph-skeleton collage-cell" {style}></div>{/each}</div>
    {:else}{#each sections as section (section.key)}{#if section.heading}<div class="visual-grid-heading">{section.heading}</div>{/if}<div class="collage-block" style={section.block}>{#each section.tiles.slice(0, Math.max(0, reach - section.start)) as [item, i, style] (item.id)}{@render tile(item, i, style)}{/each}</div>{/each}{/if}
  </div>
  {#if !choosing && !loading && !display.length}<div class="empty"><span class="empty-mark">{picked || (settled && settled.total === 0) ? '∅' : flagged ? '✓' : '∅'}</span><h2>{#if picked}<ScriptLine line={withText(corpusFault ? 'explore.empty.samplesFailed' : 'explore.empty.noOccurrenceOf', 'char', { char: picked.char })} />{:else if settled && settled.total === 0}{around('explore.empty.noOccurrenceOfTerm', 'term')[0]}{@render term()}{around('explore.empty.noOccurrenceOfTerm', 'term')[1]}{:else}{flagged ? t('explore.empty.nothingFlagged') : t('explore.empty.noCharacters')}{/if}</h2>{#if query}<button class="primary" onclick={clearQuery}>{t('explore.clearSearch')}</button>{:else}<a href={localize('/review')} class="primary">{t('explore.startRound')}</a>{/if}</div>{/if}
  {#if selected.size || bulkDone || bulkError}<BulkBar count={selected.size} bind:target={bulkTarget} busy={bulkBusy} error={bulkError} done={bulkDone}
    onapply={applyBulk} onselectall={selectShown} onclear={clearSelection} onundo={undoBulk} ondismiss={() => { bulkDone = null; bulkError = '' }} />{/if}
  <div class="scroll-sentinel" bind:this={sentinel} aria-hidden="true">{#if hasMore && loading && display.length}…{/if}</div>
  <div class="collection-bottom">{#if localName()}<span lang={locale()}>{localName()}</span>{:else}<span lang="en">GLYPH ATLAS</span>{/if}<span>{t('explore.bottom.checked', { count: data?.counts.checked })} <span class="separator">·</span> {t('explore.bottom.flagged', { count: data?.counts.flagged })}</span></div>
</section>

<style>
  .browse-unit{display:flex;gap:2px;padding:8px 8px 6px}
  .browse-unit button{border:0;border-radius:5px;background:transparent;padding:5px 10px;font-size:12px;color:var(--muted)}
  .browse-unit button[aria-pressed="true"]{background:var(--surface-selected);color:var(--ink)}
  /* The box that selects a tile shows on hover or focus, and on every tile once one is selected or on a touch screen.
     A class on the gallery says one is selected: a `:has()` there restyled every tile each time tiles were added. */
  .tile-select{position:absolute;top:8px;right:8px;z-index:4;display:flex;align-items:center;justify-content:center;width:20px;height:20px;border:1.5px solid var(--muted);border-radius:5px;background:var(--surface);color:var(--on-color);font:600 12px/1 system-ui,sans-serif;opacity:0;transition:opacity .12s}
  :global(.glyph-tile):hover .tile-select,:global(.glyph-tile):focus-visible .tile-select,.selecting .tile-select{opacity:1}
  @media(hover:none){.tile-select{opacity:.85}}
  .decided-checked .tile-select,.decided-flagged .tile-select{right:36px}
  :global(.glyph-tile.selected){outline:2px solid var(--accent);outline-offset:-2px;background:var(--accent-light)}
  :global(.glyph-tile.selected) .tile-select{opacity:1;background:var(--accent-solid);border-color:var(--accent-solid)}
  .visual-grid-heading{grid-column:1/-1;font-size:14px;padding:20px 2px 12px;color:var(--muted);background:var(--paper)}
  .tile-production{font-family:"GenZui Sans",system-ui,sans-serif;font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  /* The copy's year, the tile's one date: in the footer's small type, its digits all one width. */
  .tile-year{flex-shrink:0;font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
  .tile-production{min-width:0}
  /* On a phone the year takes the running number's place; a range wider still ends in an ellipsis. */
  @media(max-width:700px){.tile-year{flex-shrink:1;min-width:0;overflow:hidden;text-overflow:ellipsis}.tile-footer:has(.tile-year) .tile-number{display:none}}
  .tile-number{margin-left:auto}
  .tile-footer .tile-arrow{margin-left:0}
  .tile-footer .status-dot{flex-shrink:0}
  @media(max-width:700px){.tile-production{display:none}}
  .status-dot.withheld{background:transparent;box-shadow:inset 0 0 0 1px light-dark(#9b9ba3, #9d9da6)}
  .glyph-tile.decided-checked{background:var(--good-light)}
  .glyph-tile.decided-flagged{background:var(--wrong-light)}
  .glyph-tile.decided-checked:hover{background:light-dark(#e0ece4, rgb(130 205 163 / 18%))}
  .glyph-tile.decided-flagged:hover{background:light-dark(#fbe3e6, rgb(255 146 159 / 18%))}
  /* Drawn above the hover panel, so the outline stays whole while the details show. */
  .decided-checked::after,.decided-flagged::after{content:'';position:absolute;inset:0;z-index:3;border:2px solid var(--good);pointer-events:none}
  .decided-flagged::after{border-color:var(--wrong)}
  .decided-checked .tile-label,.decided-flagged .tile-label{right:38px;overflow:hidden;white-space:nowrap}
  .tile-verdict{position:absolute;top:8px;right:8px;z-index:3;display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;color:var(--on-color);font:600 13px/1 system-ui,sans-serif}
  .decided-checked .tile-verdict{background:var(--good-solid)}
  .decided-flagged .tile-verdict{background:var(--wrong-solid)}
  @media(max-width:700px){.tile-verdict{top:6px;right:6px;width:18px;height:18px;font-size:11px}.decided-checked .tile-label,.decided-flagged .tile-label{right:28px}}
</style>
