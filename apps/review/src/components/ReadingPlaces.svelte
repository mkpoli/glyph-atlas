<script>
  // The other places a run is written, to say one by one that it is read there as it is here. Each shows its
  // page around the run. A place that is read already shows its reading, and nothing is written at any
  // place but by its own button.
  import { onMount } from 'svelte'
  import { setReading } from '../lib/cropReadings.js'
  import { bookOf, placeReading, placesText, readingPlaces, readingsNow, runPlaces } from '../lib/readingPlaces.js'
  import { t } from '../lib/i18n.svelte.js'
  import ReadingRuby from './ReadingRuby.svelte'
  import RunImage from './RunImage.svelte'

  // `first` is a first page already read; `word` is the dictionary word the reader holds on the run.
  let { item, value, word = null, first = null, close } = $props()
  const METHODS = ['context', 'ruby', 'parallel']
  const uid = $props.id()
  const text = $derived(placesText(value)), book = $derived(bookOf(item))
  let dialog = $state(null), places = $state([]), works = $state([]), total = $state(0), next = $state(0), document = $state(null)
  let loading = $state(false), error = $state(''), carry = $state(true), methods = $state({}), saving = $state(null), refused = $state({})
  let generation = 0
  const here = $derived(works.find(work => work.id === book)?.count ?? 0)
  const all = $derived(works.reduce((sum, work) => sum + work.count, 0))
  const more = $derived(next < total)

  async function load({ page = null, reset = false } = {}) {
    const current = reset ? ++generation : generation
    loading = true; error = ''
    try {
      const found = page ?? await runPlaces(text, { document, offset: reset ? 0 : next })
      const reads = found.items.length ? await readingsNow([...new Set(found.items.flatMap(one => one.crops.map(crop => crop.id)))]) : new Map()
      if (current !== generation) return
      const listed = readingPlaces(found.items, reads, item.id)
      places = reset ? listed : [...places, ...listed]
      next = found.next_offset
      if (found.total != null) { total = found.total; if (!document) works = found.works ?? [] }
    } catch (failure) { if (current === generation) error = failure.message || t('client.saveFailed') }
    finally { if (current === generation) loading = false }
  }
  function within(one) { if (document !== one) { document = one; load({ reset: true }) } }
  async function confirm(place) {
    if (saving) return
    saving = place.id; refused = { ...refused, [place.id]: '' }
    try {
      const method = methods[place.id] ?? 'context'
      const [crop, reading] = placeReading(place, value, { method, word: word && carry ? { id: word.word, form: word.form ?? 'certain', method } : undefined })
      const saved = await setReading(crop, reading)
      places = places.map(one => one.id === place.id ? { ...one, open: false, read: saved.reading } : one)
    } catch (failure) { refused = { ...refused, [place.id]: failure.message || t('client.saveFailed') } }
    finally { saving = null }
  }
  const where = place => [place.crops[0].source?.title ?? place.crops[0].source, place.crops[0].page_number ? t('tile.page', { page: place.crops[0].page_number }) : ''].filter(part => typeof part === 'string' && part).join(' · ')
  // The line a place's source gives, about its run where the run stands on it once.
  function around(place) {
    const line = (place.crops[0].text ?? '').normalize('NFC'), run = place.run.join(''), at = line.indexOf(run)
    return line && at >= 0 && line.indexOf(run, at + 1) < 0 ? { before: line.slice(0, at), run, after: line.slice(at + run.length) } : line ? { before: line, run: '', after: '' } : null
  }
  onMount(async () => {
    dialog.showModal()
    await load({ page: first, reset: true })
    // A book that writes the run elsewhere too is read first.
    if (here > 1 && all > here) within(book)
  })
</script>

<dialog class="reading-places" bind:this={dialog} aria-labelledby="{uid}-title" onclose={close} onclick={event => { if (event.target === dialog) dialog.close() }}>
  <header>
    <h2 id="{uid}-title"><ReadingRuby {value} /> <span>{t('reading.places')}</span></h2>
    <button type="button" class="icon-button" aria-label={t('structure.close')} onclick={() => dialog.close()}>×</button>
  </header>
  {#if word || (here > 1 && all > here)}<div class="places-tools">
    {#if here > 1 && all > here}
      <div class="places-scope" role="group">
        <button type="button" aria-pressed={document === book} onclick={() => within(book)}>{t('reading.places.book')} {here - 1}</button>
        <button type="button" aria-pressed={document === null} onclick={() => within(null)}>{t('reading.places.all')} {all - 1}</button>
      </div>
    {/if}
    {#if word}<button type="button" role="switch" aria-checked={carry} onclick={() => carry = !carry}>{t('word.label')} {word.reading ?? word.word}</button>{/if}
  </div>{/if}
  <ul aria-busy={loading}>
    {#each places as place (place.id)}
      {@const line = around(place)}
      <li class:done={!place.open}>
        <span class="place-page"><RunImage crops={place.crops} page={place.page} vertical={place.vertical} context /></span>
        <div class="place-body">
          {#if line}<p lang="ja">{line.before}{#if line.run}<mark>{line.run}</mark>{/if}{line.after}</p>{/if}
          <span class="place-where">{where(place)}</span>
          {#if place.open}
            <div class="place-methods" role="radiogroup" aria-label={t('reading.method')}>
              {#each METHODS as method}<label class:chosen={(methods[place.id] ?? 'context') === method}><input type="radio" name="{uid}-{place.id}" value={method} checked={(methods[place.id] ?? 'context') === method}
                disabled={Boolean(saving)} onchange={() => methods = { ...methods, [place.id]: method }} />{t(`reading.methods.${method}`)}</label>{/each}
            </div>
            <button type="button" class="place-confirm" disabled={Boolean(saving)} onclick={() => confirm(place)}>{saving === place.id ? t('common.saving') : t('reading.places.confirm', { reading: value.reading.join('') })}</button>
          {:else if place.read}
            <span class="place-read">{#each place.read.values as one}<ReadingRuby value={one} />{/each}{#if place.read.status === 'disputed'}<span class="disputed">{t('reading.disputed')}</span>{/if}</span>
          {/if}
          {#if refused[place.id]}<span class="place-error" role="alert">{refused[place.id]}</span>{/if}
        </div>
      </li>
    {/each}
  </ul>
  {#if error}<p class="place-error" role="alert">{error}</p>{/if}
  {#if !loading && !error && !places.length}<p class="places-none">{t('reading.places.none')}</p>{/if}
  {#if more || loading}<button type="button" class="places-more" disabled={loading} onclick={() => load()}>{loading ? '…' : t('explore.runs.more')}</button>{/if}
</dialog>

<style>
  .reading-places{inset:0;margin:auto;width:min(760px,calc(100vw - 32px));height:fit-content;max-height:calc(100dvh - 32px);padding:20px 24px 24px;
    border:1px solid var(--line);border-radius:12px;background:var(--surface);color:var(--ink);box-shadow:0 20px 60px var(--shadow);overflow:auto;overscroll-behavior:contain}
  .reading-places[open]{display:flex;flex-direction:column;gap:16px}
  .reading-places::backdrop{background:light-dark(#16151f40, rgb(0 0 0 / 50%))}
  header{display:flex;align-items:center;justify-content:space-between;gap:12px}
  h2{display:flex;align-items:baseline;gap:12px;margin:0;font-size:13px;font-weight:500;color:var(--muted)}
  h2 :global(.reading-run){font-family:var(--char-font);font-size:26px;line-height:1.25;color:var(--ink)}
  .places-tools{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:12px}
  .places-scope{display:flex;gap:6px}
  button{font:inherit;font-size:12px;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:5px 11px;cursor:pointer}
  button:hover:not(:disabled){border-color:var(--line-strong)}
  button:disabled{opacity:.5;cursor:default}
  button:focus-visible{outline:3px solid var(--focus-ring);outline-offset:1px}
  [aria-pressed=true],[role=switch][aria-checked=true]{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
  ul{list-style:none;margin:0;padding:0;display:grid;gap:12px}
  li{display:grid;grid-template-columns:150px minmax(0,1fr);gap:16px;border:1px solid var(--line);border-radius:8px;padding:12px}
  .place-page{display:block;height:220px;border-radius:6px;overflow:hidden;background:var(--crop-paper)}
  .place-page :global(svg){display:block;width:100%;height:100%}
  .place-body{display:flex;flex-direction:column;align-items:flex-start;gap:10px;min-width:0;font-size:12px;color:var(--muted)}
  .place-body p{margin:0;font-family:var(--char-font);font-size:16px;line-height:1.7;color:var(--ink);overflow-wrap:anywhere}
  mark{background:var(--accent-light);color:var(--accent);border-radius:3px}
  .place-methods{display:flex;flex-wrap:wrap;gap:6px}
  .place-methods label{position:relative;border:1px solid var(--line);border-radius:999px;padding:4px 11px;color:var(--ink);cursor:pointer}
  .place-methods label:hover{border-color:var(--line-strong)}
  .place-methods label.chosen{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
  .place-methods label:has(:focus-visible){outline:3px solid var(--focus-ring);outline-offset:1px}
  .place-methods input{position:absolute;inset:0;opacity:0;margin:0;cursor:pointer}
  .place-confirm{background:var(--accent-solid);border-color:var(--accent-solid);color:#fff;padding:7px 16px}
  .place-read{display:inline-flex;flex-wrap:wrap;gap:12px;align-items:center}
  .place-read :global(.reading-run){font-family:var(--char-font);font-size:20px;line-height:1.25;color:var(--ink)}
  .disputed,.place-error{color:var(--wrong)}
  .place-error,.places-none{margin:0;font-size:12px}
  .places-none{color:var(--muted)}
  .places-more{align-self:center}
  @media(max-width:520px){.reading-places{padding:16px}li{grid-template-columns:96px minmax(0,1fr);gap:12px}.place-page{height:160px}}
</style>
