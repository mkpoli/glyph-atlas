<script>
  // A character's crops along its time axis (編年). Each crop stands at the decade its book's date begins
  // in. The overview draws the whole span to scale and counts every decade's crops; the strip below it
  // shows a sample of each decade, a decade always the same width, and folds a stretch of fifty years or
  // more with no crop into a marked gap. The crops no source gives a year have a lane of their own. The axis places crops by when their copy was made, or by when their text
  // was composed, and can be narrowed to a script, a style group or a kind of production.
  import { untrack } from 'svelte'
  import { replaceState } from '$app/navigation'
  import ScriptText from '../components/ScriptText.svelte'
  import Glyph from '../components/Glyph.svelte'
  import SiteLinks from '../components/SiteLinks.svelte'
  import { t, localize, formatNumber, formatYear } from '../lib/i18n.svelte.js'
  import { chronology, chronologyAddress, PRODUCTIONS } from '../lib/chronology.js'
  import { characterAddress } from '../lib/gallery.js'
  import { STYLE_GROUPS } from '../lib/style.js'
  import { dateLabel, eventLabel, sourceName, HUTIME_FORM } from '../lib/dating.js'

  let { card, options, first, inspect } = $props()
  let chosen = $state(untrack(() => ({ ...options }))), axis = $state(untrack(() => first)), loading = $state(false), failed = $state(''), request = 0

  // One decade is this many pixels wide in the strip; a folded gap is GAP wide, and folds at least FOLD decades.
  const DECADE = 64, GAP = 40, FOLD = 5
  const buckets = $derived(axis.buckets)
  const from = $derived(buckets.length ? Math.floor(buckets[0].decade / 100) * 100 : 0)
  const to = $derived(buckets.length ? Math.ceil((buckets.at(-1).decade + 10) / 100) * 100 : 0)
  // The overview's scale, the whole span from the first century to the last.
  const share = year => (year - from) / Math.max(1, to - from) * 100
  // The strip: each decade from the first with a crop to the last, a long empty stretch folded.
  const strip = $derived.by(() => {
    const at = new Map(), gaps = [], lines = []
    if (!buckets.length) return { width: 0, at, gaps, lines }
    const present = new Set(buckets.map(b => b.decade))
    let x = 0, decade = buckets[0].decade
    const last = buckets.at(-1).decade
    while (decade <= last) {
      if (!present.has(decade)) {
        let end = decade
        while (end <= last && !present.has(end)) end += 10
        if ((end - decade) / 10 >= FOLD) { gaps.push({ from: decade, to: end - 1, x }); x += GAP; decade = end; continue }
      }
      at.set(decade, x)
      if (decade % 100 === 0 || x === 0 || gaps.at(-1)?.x + GAP === x) lines.push({ year: decade, x, century: decade % 100 === 0 })
      x += DECADE; decade += 10
    }
    return { width: x, at, gaps, lines }
  })
  const centuries = $derived(buckets.length ? Array.from({ length: (to - from) / 100 + 1 }, (_, i) => from + i * 100) : [])
  const count = bucket => bucket.local + bucket.corpus
  const most = $derived(Math.max(1, ...buckets.map(count)))
  const dated = $derived(buckets.reduce((n, b) => n + count(b), 0))
  const undated = $derived(count(axis.undated))
  // The crops the inspector steps through, oldest first and the undated last, as they are shown.
  const queue = $derived([...buckets.flatMap(b => b.items), ...axis.undated.items])
  const scripts = $derived([...new Set((card.characters ?? []).map(c => c.script).filter(Boolean))])
  const chars = $derived(chosen.scope === 'grapheme' && chosen.script ? (card.characters ?? []).filter(c => c.script === chosen.script).map(c => c.char) : [])

  async function set(change) {
    chosen = { ...chosen, ...change }
    if ('scope' in change) chosen.script = ''
    replaceState(localize(chronologyAddress(card.code_point, chosen)), {})
    const id = ++request
    loading = true; failed = ''
    try {
      const found = await chronology(card.code_point, { ...chosen, chars })
      if (id === request) axis = found
    } catch (e) { if (id === request) failed = e.message }
    finally { if (id === request) loading = false }
  }
  const open = item => inspect(item.id, null, queue, null, item.origin === 'corpus' ? 'corpus' : 'collection')
  let track = $state(), overviewWidth = $state(1200)
  // The overview names every century it has room for, at least 44 pixels apart.
  const tickStep = $derived(Math.max(1, Math.ceil(centuries.length * 44 / Math.max(1, overviewWidth))))
  const jump = decade => track?.scrollTo({ left: Math.max(0, (strip.at.get(decade) ?? 0) - DECADE), behavior: 'smooth' })
  // The strip shows no scrollbar: ‹ › turn it by most of its width, and a touch or a wheel still moves it.
  let scrolled = $state(0), shownWidth = $state(0)
  const turn = direction => track?.scrollBy({ left: direction * Math.max(DECADE, shownWidth - 2 * DECADE), behavior: 'smooth' })
  // The gallery of one decade's crops, oldest first, in the same style group. The gallery places crops
  // by their copy's date and narrows by style alone, so with the text's date, a production or a script
  // chosen it would show other crops than the count, and the count links nowhere.
  const linkable = $derived(chosen.axis !== 'composed' && !chosen.production && !chosen.script)
  const decadeGallery = decade => localize(characterAddress(card.code_point, { scope: chosen.scope === 'grapheme' ? 'family' : 'exact',
    style: chosen.style, order: 'year', years: `${decade}-${decade + 9}` }))
  const undatedGallery = $derived(localize(characterAddress(card.code_point, { scope: chosen.scope === 'grapheme' ? 'family' : 'exact', style: chosen.style, years: 'undated' })))
  const title = item => {
    const date = item.dating?.[chosen.axis === 'composed' ? 'composed' : 'witness']
    const work = typeof item.source === 'string' ? item.source : item.source?.title
    return [item.label, date ? `${eventLabel(chosen.axis === 'composed' ? 'composed' : date.kind, date)} (${date.text}, ${sourceName(date.source)})` : t('date.undated'), work]
      .filter(Boolean).join('\n')
  }
  const decadeName = decade => dateLabel({ start: decade, end: decade + 9, precision: 'decade' })
</script>

{#snippet chips(label, values, value, name, key)}
  <div class="choice" role="group" aria-label={label}>
    <span class="choice-label">{label}</span>
    <button class:active={!value} aria-pressed={!value} onclick={() => set({ [key]: '' })}>{t('chronology.all')}</button>
    {#each values as option (option)}<button class:active={value === option} aria-pressed={value === option} onclick={() => set({ [key]: option })}>{name(option)}</button>{/each}
  </div>
{/snippet}

{#snippet crop(item)}
  <button class="crop" title={title(item)} aria-label={title(item).replaceAll('\n', ', ')} onclick={() => open(item)}>
    {#if item.image && item.proxyable !== false}<Glyph {item} alt="" class="crop-image" />{:else}<span class="crop-missing"><ScriptText text={item.label} titled={false} /></span>{/if}
    {#if item.written_form && item.written_form !== item.label}<span class="crop-form"><ScriptText text={item.written_form} titled={false} /></span>{/if}
  </button>
{/snippet}

<section class="explore chronology" aria-busy={loading}>
  <div class="page-status"><SiteLinks /></div>
  <header class="heading">
    <a class="quiet-link" href={localize(characterAddress(card.code_point, { scope: chosen.scope === 'grapheme' ? 'family' : 'exact' }))}>← {t('chronology.gallery')}</a>
    <h1><span class="glyph"><ScriptText text={card.char} script={card.script} /></span><span class="name">{t('nav.chronology')}</span></h1>
    <p class="summary">{t('chronology.dated', { count: dated })}{#if undated}{' · '}{t('chronology.yearless', { count: undated })}{/if}{#if buckets.length}{' · '}{dateLabel({ start: buckets[0].decade, end: buckets.at(-1).decade + 9, precision: 'years' })}{/if}</p>
  </header>

  <div class="controls">
    <div class="choice" role="group" aria-label={t('chronology.axis.label')}>
      <span class="choice-label">{t('chronology.axis.label')}</span>
      <button class:active={chosen.axis !== 'composed'} aria-pressed={chosen.axis !== 'composed'} onclick={() => set({ axis: '' })}>{t('chronology.axis.witness')}</button>
      <button class:active={chosen.axis === 'composed'} aria-pressed={chosen.axis === 'composed'} onclick={() => set({ axis: 'composed' })}>{t('chronology.axis.composed')}</button>
    </div>
    {#if card.grapheme?.character_count > 1 || (card.characters ?? []).length > 1}
      <div class="choice" role="group" aria-label={t('chronology.scope.label')}>
        <span class="choice-label">{t('chronology.scope.label')}</span>
        <button class:active={chosen.scope !== 'grapheme'} aria-pressed={chosen.scope !== 'grapheme'} onclick={() => set({ scope: '' })}>{t('chronology.scope.character')}</button>
        <button class:active={chosen.scope === 'grapheme'} aria-pressed={chosen.scope === 'grapheme'} onclick={() => set({ scope: 'grapheme' })}>{t('chronology.scope.grapheme')}</button>
      </div>
    {/if}
    {#if chosen.scope === 'grapheme' && scripts.length > 1}{@render chips(t('chronology.filter.script'), scripts, chosen.script, script => t(`script.${script}`), 'script')}{/if}
    {@render chips(t('chronology.filter.style'), STYLE_GROUPS, chosen.style, group => group === 'unassessed' ? t('style.kind.unassessed') : t(`styleFilter.${group}`), 'style')}
    {@render chips(t('chronology.filter.production'), PRODUCTIONS, chosen.production, kind => t(`production.kind.${kind}`), 'production')}
  </div>

  {#if failed}<div class="error-message" role="alert">{failed}<button onclick={() => set({})}>{t('common.tryAgain')}</button></div>{/if}

  {#if buckets.length}
    <!-- Every decade's crops on one line, to the axis's scale; a decade's bar moves the axis to it. -->
    <figure class="overview" aria-label={t('chronology.overview')} bind:clientWidth={overviewWidth}>
      <div class="overview-bars">
        {#each buckets as bucket (bucket.decade)}
          <button class="bar" style:left="{share(bucket.decade)}%" style:width="max(2px, {share(from + 10)}%)"
                  style:height="{Math.max(2, count(bucket) / most * 44)}px" tabindex="-1" onclick={() => jump(bucket.decade)}
                  aria-label={t('chronology.bar', { decade: decadeName(bucket.decade), count: count(bucket) })} title={t('chronology.bar', { decade: decadeName(bucket.decade), count: count(bucket) })}></button>
        {/each}
      </div>
      <div class="overview-ticks" aria-hidden="true">
        {#each centuries as year, i (year)}{#if i % tickStep === 0}<span style:left="{share(year)}%">{formatYear(year)}</span>{/if}{/each}
      </div>
    </figure>

    <div class="strip">
    <div class="turns">{#if strip.width > shownWidth}
      <button class="turn earlier" aria-label={t('chronology.earlier')} disabled={scrolled <= 0} onclick={() => turn(-1)}>‹</button>
      <button class="turn later" aria-label={t('chronology.later')} disabled={scrolled >= strip.width - shownWidth - 1} onclick={() => turn(1)}>›</button>
    {/if}</div>
    <div class="track" bind:this={track} bind:clientWidth={shownWidth} onscroll={() => scrolled = track.scrollLeft}>
      <div class="track-inner" style:width="{strip.width}px">
        {#each strip.lines as line (line.year)}<span class="century" class:faint={!line.century} style:left="{line.x}px"><span>{formatYear(line.year)}</span></span>{/each}
        {#each strip.gaps as gap (gap.from)}<span class="gap" style:left="{gap.x}px" style:width="{GAP}px" title={dateLabel({ start: gap.from, end: gap.to, precision: 'years' })}
          role="img" aria-label={t('chronology.gap', { range: dateLabel({ start: gap.from, end: gap.to, precision: 'years' }) })}><span>{formatYear(gap.from)}–{formatYear(gap.to)}</span></span>{/each}
        {#each buckets as bucket (bucket.decade)}
          <div class="decade" style:left="{strip.at.get(bucket.decade)}px" style:width="{DECADE}px">
            {#if linkable}<a class="decade-count" href={decadeGallery(bucket.decade)} title={t('chronology.bar', { decade: decadeName(bucket.decade), count: count(bucket) })}
              aria-label={t('chronology.bar', { decade: decadeName(bucket.decade), count: count(bucket) })}>{formatNumber(count(bucket))}</a>
            {:else}<span class="decade-count" title={t('chronology.bar', { decade: decadeName(bucket.decade), count: count(bucket) })}>{formatNumber(count(bucket))}</span>{/if}
            {#each bucket.items as item (item.id)}{@render crop(item)}{/each}
            {#if count(bucket) > bucket.items.length}{#if linkable}<a class="decade-more" href={decadeGallery(bucket.decade)}>+{formatNumber(count(bucket) - bucket.items.length)}</a>{:else}<span class="decade-more">+{formatNumber(count(bucket) - bucket.items.length)}</span>{/if}{/if}
          </div>
        {/each}
      </div>
    </div>
    </div>
  {:else if !loading}
    <p class="empty">{chosen.axis === 'composed' ? t('chronology.empty.composed') : t('chronology.empty')}</p>
  {/if}

  {#if undated}
    <section class="undated">
      <h2>{t('date.noYear')} <small>{formatNumber(undated)}</small></h2>
      <div class="undated-crops">{#each axis.undated.items as item (item.id)}{@render crop(item)}{/each}
        {#if undated > axis.undated.items.length}{#if linkable}<a class="decade-more" href={undatedGallery}>+{formatNumber(undated - axis.undated.items.length)}</a>{:else}<span class="decade-more">+{formatNumber(undated - axis.undated.items.length)}</span>{/if}{/if}</div>
    </section>
  {/if}

  <p class="method">{t('chronology.method')} <a href={HUTIME_FORM} target="_blank" rel="noreferrer">{t('chronology.hutime')}</a></p>
</section>

<style>
  .chronology{padding-bottom:48px}
  .chronology[aria-busy="true"] .track,.chronology[aria-busy="true"] .overview,.chronology[aria-busy="true"] .undated{opacity:.55;transition:opacity .2s}
  .heading{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--line);padding:18px 0 14px}
  h1{display:flex;align-items:baseline;gap:14px;margin:0;font-weight:500}
  .glyph{font-size:48px;line-height:1}
  .name{font-size:15px;color:var(--muted);letter-spacing:.04em}
  .summary{margin:0;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
  .controls{display:flex;flex-wrap:wrap;gap:10px 22px;padding:4px 0 18px;border-bottom:1px solid var(--line)}
  .choice{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
  .choice-label{font-size:11px;color:var(--faint);margin-right:2px}
  .choice button{border:1px solid var(--line);background:transparent;border-radius:7px;padding:5px 10px;font-size:12px;white-space:nowrap}
  .choice button.active{border-color:var(--accent);background:var(--accent-light);color:var(--accent)}
  .overview{margin:22px 0 0;padding:0}
  .overview-bars{position:relative;height:46px;border-bottom:1px solid var(--line-strong)}
  .bar{position:absolute;bottom:0;padding:0;border:0;border-radius:2px 2px 0 0;background:var(--accent);opacity:.55;transform:translateX(0)}
  .bar:hover,.bar:focus-visible{opacity:1}
  .overview-ticks{position:relative;height:18px;font-size:10px;color:var(--faint);font-variant-numeric:tabular-nums}
  .overview-ticks span{position:absolute;top:4px;transform:translateX(-50%)}
  .overview-ticks span:first-child{transform:none}
  .overview-ticks span:last-child:not(:first-child){transform:translateX(-100%)}
  .strip{margin-top:10px}
  .turns{display:flex;justify-content:flex-end;gap:4px;height:28px;margin-bottom:4px}
  .track{overflow-x:auto;overflow-y:hidden;border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:var(--surface-subtle);scrollbar-width:none}
  .track::-webkit-scrollbar{display:none}
  .turn{width:28px;height:28px;display:flex;align-items:center;justify-content:center;padding:0;border:1px solid var(--line);border-radius:6px;background:var(--surface-subtle);color:var(--muted);font-size:16px;line-height:1}
  .turn:hover:not(:disabled){color:var(--accent)}.turn:disabled{color:var(--faint);opacity:.5}
  .track-inner{position:relative;height:calc(28px + 6 * 60px + 40px);min-width:100%}
  .century{position:absolute;top:0;bottom:0;border-left:1px solid var(--line)}
  .century span{position:absolute;top:6px;left:6px;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .century.faint{border-left-style:dotted}
  .century.faint span{color:var(--faint)}
  /* A folded stretch: two hairlines and its years written down the gap. */
  .gap{position:absolute;top:0;bottom:0;border-left:1px dashed var(--line-strong);border-right:1px dashed var(--line-strong);background:repeating-linear-gradient(135deg,transparent 0 6px,var(--line) 6px 7px)}
  .gap span{position:absolute;top:34px;left:50%;writing-mode:vertical-rl;transform:translateX(-50%);font-size:10px;color:var(--faint);font-variant-numeric:tabular-nums;white-space:nowrap;background:var(--surface-subtle);padding:4px 0}
  .decade{position:absolute;top:28px;display:flex;flex-direction:column;align-items:center;gap:4px;padding:0 4px}
  .decade-count{font-size:10px;color:var(--faint);text-decoration:none;font-variant-numeric:tabular-nums;line-height:16px}
  .decade-count:hover{color:var(--accent)}
  .crop{position:relative;flex:0 0 auto;width:56px;height:56px;padding:3px;border:1px solid transparent;border-radius:6px;background:var(--surface-tile)}
  .crop:hover,.crop:focus-visible{border-color:var(--accent)}
  .crop :global(.crop-image){width:100%;height:100%}
  .crop-missing{display:flex;width:100%;height:100%;align-items:center;justify-content:center;font-size:20px;color:var(--muted)}
  .crop-form{position:absolute;right:2px;bottom:1px;font-size:10px;line-height:1;color:var(--accent)}
  .decade-more{font-size:10px;color:var(--muted);text-decoration:none;font-variant-numeric:tabular-nums}
  .decade-more:hover{color:var(--accent)}
  .undated{margin-top:26px}
  .undated h2{margin:0 0 10px;font-size:13px;font-weight:500;color:var(--muted)}
  .undated small{margin-left:6px;color:var(--faint);font-variant-numeric:tabular-nums}
  .undated-crops{display:flex;flex-wrap:wrap;align-items:center;gap:4px;padding:10px;border:1px dashed var(--line);border-radius:8px;min-height:76px}
  .empty{padding:40px 0;color:var(--muted);font-size:13px}
  .method{margin:26px 0 0;font-size:11px;line-height:1.6;color:var(--faint);max-width:72ch}
  .method a{color:inherit;text-decoration:underline;text-underline-offset:2px}
  @media(max-width:700px){.glyph{font-size:40px}.controls{gap:8px}.crop{width:48px;height:48px}.track-inner{height:calc(28px + 6 * 52px + 40px)}}
</style>
