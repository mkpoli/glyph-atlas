<script module>
  // The palette is one file for every editor on the page, read once and kept.
  let palette = null
  const read = () => palette ??= fetch('/palette/ids-parts.json')
    .then(response => response.ok ? response.json() : Promise.reject(new Error(`ids-parts.json: ${response.status}`)))
    .then(({ columns, parts }) => parts.map(row => Object.fromEntries(columns.map((name, i) => [name, row[i]]))))
    .catch(error => { palette = null; throw error })
</script>

<script>
  // The parts of ideographic description sequences that are hard to type (難輸入部件), built by
  // scripts/build_ids_parts.py: Common holds the parts that are 常用漢字 or in the first level of the
  // 通用规范汉字表, most used first; Difficult the rest, by stroke count and then by the class of the
  // first stroke (横 豎 撇 點 折, where KanjiVG records it, unknown last); Strokes the CJK strokes; Recent
  // the parts picked here before, kept in this browser. A filter of characters finds those parts, a
  // number the parts of that many strokes. Picking a part calls `onpick(part)`.
  import { stored, remember } from '../lib/client.js'
  import { t } from '../lib/i18n.svelte.js'
  let { disabled = false, onpick } = $props()
  const uid = $props.id()

  const RECENT_KEY = 'atlas.ids.recentParts'
  const RECENT_MAX = 30
  const CLASSES = ['横', '豎', '撇', '點', '折']
  const CLASS_KEY = { 横: 'h', 豎: 's', 撇: 'p', 點: 'd', 折: 'z' }
  const TABS = ['common', 'difficult', 'strokes', 'recent']

  let parts = $state(null), failed = $state(false)
  let tab = $state('common'), count = $state(undefined), filter = $state('')
  let recent = $state([])
  $effect(() => {
    const kept = stored(RECENT_KEY, [])
    recent = Array.isArray(kept) ? [...new Set(kept.filter(part => typeof part === 'string'))].slice(0, RECENT_MAX) : []
    read().then(found => { parts = found }, () => { failed = true })
  })

  const byPart = $derived(new Map((parts ?? []).map(part => [part.part, part])))
  const difficult = $derived((parts ?? []).filter(part => part.kind === 'd'))
  // Stroke counts that Difficult holds, in order, null (not recorded) last; `count` is the one chosen,
  // undefined until one is, so the first count shows.
  const counts = $derived([...new Set(difficult.map(part => part.strokes))].sort((a, b) => (a ?? Infinity) - (b ?? Infinity)))
  const shownCount = $derived(counts.includes(count) ? count : counts[0])
  const grouped = list => [...CLASSES, null]
    .map(cls => ({ cls, parts: list.filter(part => part.first === cls) }))
    .filter(group => group.parts.length)

  const query = $derived(filter.trim())
  const filtered = $derived.by(() => {
    if (!query || !parts) return null
    if (/^\d+$/.test(query)) return parts.filter(part => part.strokes === Number(query))
    const chars = new Set([...query])
    return parts.filter(part => chars.has(part.part))
  })

  const shown = $derived.by(() => {
    if (!parts) return []
    if (tab === 'common') return [{ cls: undefined, parts: parts.filter(part => part.kind === 'c') }]
    if (tab === 'difficult') return grouped(difficult.filter(part => part.strokes === shownCount))
    if (tab === 'strokes') return grouped(parts.filter(part => part.kind === 's'))
    return [{ cls: undefined, parts: recent.map(part => byPart.get(part) ?? { part }) }]
  })

  function pick(part) {
    recent = [part, ...recent.filter(other => other !== part)].slice(0, RECENT_MAX)
    remember(RECENT_KEY, recent)
    onpick?.(part)
  }
  const title = part => [
    part.part,
    part.strokes == null ? t('form.parts.unknownStrokes') : t('form.parts.strokeCount', { count: part.strokes }),
    part.uses ? t('form.parts.uses', { count: part.uses }) : null,
  ].filter(Boolean).join(' · ')
  // Arrow keys, Home and End move between the tabs and choose the one reached.
  let tabButtons = $state([])
  function tabKey(event, index) {
    const last = TABS.length - 1
    const to = { ArrowRight: index === last ? 0 : index + 1, ArrowLeft: index === 0 ? last : index - 1, Home: 0, End: last }[event.key]
    if (to === undefined) return
    event.preventDefault()
    tab = TABS[to]
    tabButtons[to]?.focus()
  }
  // A stroke outside the five classes (㇣) is another kind of stroke; any other part lacks the record.
  const heading = cls => cls !== null ? t(`form.parts.first.${CLASS_KEY[cls]}`) : tab === 'strokes' && !query ? t('form.parts.first.other') : t('form.parts.first.unknown')
</script>

{#snippet grid(list)}
  <div class="parts-grid">
    {#each list as part (part.part)}
      <button type="button" class="part" {disabled} lang="ja" title={title(part)} aria-label={title(part)} onclick={() => pick(part.part)}>{part.part}</button>
    {/each}
  </div>
{/snippet}

<div class="ids-parts" role="group" aria-label={t('form.parts.label')}>
  <div class="parts-top">
    <div class="parts-tabs" role="tablist" aria-label={t('form.parts.label')}>
      {#each TABS as name, index (name)}
        <button type="button" role="tab" id={`${uid}-tab-${name}`} aria-controls={`${uid}-panel`} bind:this={tabButtons[index]}
          class:active={tab === name} aria-selected={tab === name} tabindex={tab === name ? 0 : -1}
          onclick={() => { tab = name; filter = '' }} onkeydown={event => tabKey(event, index)}>{t(`form.parts.tab.${name}`)}</button>
      {/each}
    </div>
    <input class="parts-filter" type="search" bind:value={filter} lang="ja" spellcheck="false" autocomplete="off"
      placeholder={t('form.parts.filter')} aria-label={t('form.parts.filter')} />
  </div>
  <div class="parts-panel" role="tabpanel" id={`${uid}-panel`} aria-labelledby={`${uid}-tab-${tab}`}>
    {#if failed}
      <p class="parts-note" role="status">{t('form.parts.failed')}</p>
    {:else if !parts}
      <p class="parts-note" role="status">{t('form.parts.loading')}</p>
    {:else if filtered}
      {#if filtered.length}{@render grid(filtered)}{:else}<p class="parts-note">{t('form.parts.none')}</p>{/if}
    {:else}
      {#if tab === 'difficult'}
        <div class="parts-counts" role="group" aria-label={t('form.parts.byStrokes')}>
          {#each counts as value (value ?? 'unknown')}
            <button type="button" class:active={value === shownCount} aria-pressed={value === shownCount}
              title={value == null ? t('form.parts.unknownStrokes') : t('form.parts.strokeCount', { count: value })}
              onclick={() => count = value}>{value ?? '?'}</button>
          {/each}
        </div>
      {/if}
      {#if tab === 'recent' && !recent.length}
        <p class="parts-note">{t('form.parts.noRecent')}</p>
      {/if}
      {#each shown as group (group.cls ?? 'all')}
        {#if group.cls !== undefined}<h4 class="parts-class">{heading(group.cls)}</h4>{/if}
        {@render grid(group.parts)}
      {/each}
    {/if}
  </div>
</div>

<style>
  /* Plangothic and Noto Sans CJK JP, subset to the parts few installed fonts draw; see static/fonts/README.md. */
  @font-face{font-family:"IDS Parts";src:url("/fonts/Plangothic-ids-parts.woff2") format("woff2");font-display:swap;
    unicode-range:U+2E80-2EFF,U+31E4-31EF,U+20000-3FFFF}
  @font-face{font-family:"IDS Strokes";src:url("/fonts/NotoSansCJKjp-ids-strokes.woff2") format("woff2");font-display:swap;
    unicode-range:U+31C0-31E3}
  .ids-parts{display:flex;flex-direction:column;gap:8px;min-width:0}
  .parts-top{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
  .parts-tabs{display:flex;flex-wrap:wrap;gap:4px}
  .parts-tabs button,.parts-counts button{border:0;background:transparent;font-size:12px;padding:6px 10px;border-radius:5px;color:var(--muted)}
  .parts-tabs button.active,.parts-counts button.active{background:var(--surface-selected);color:var(--ink)}
  .parts-filter{flex:1 1 100%;min-width:0;padding:6px 8px;font-size:13px}
  .parts-counts{display:flex;flex-wrap:wrap;gap:2px}
  .parts-counts button{min-width:30px;padding:4px 6px;font-variant-numeric:tabular-nums}
  .parts-panel{display:flex;flex-direction:column;gap:8px;min-width:0}
  /* The subset faces first: their ranges cover only the strokes and rare parts, so other text falls
     through to the page's own fonts. */
  .parts-class,.parts-filter{font-family:"IDS Strokes","IDS Parts",Inter,"Helvetica Neue",Arial,"Noto Sans","GenZui Sans",sans-serif}
  .parts-class{margin:4px 0 0;font-size:11px;font-weight:500;color:var(--muted)}
  .parts-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(38px,1fr));gap:3px;max-height:260px;overflow:auto}
  .part{display:flex;align-items:center;justify-content:center;aspect-ratio:1;padding:0;font-size:22px;line-height:1;
    border-color:transparent;background:var(--surface-subtle);font-family:"IDS Parts","IDS Strokes",sans-serif}
  .part:not(:disabled):hover{border-color:var(--accent);background:var(--accent-light)}
  .parts-note{margin:0;font-size:12px;color:var(--muted)}
</style>
