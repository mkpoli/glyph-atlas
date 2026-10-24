<script>
  // "Take parts from": the characters or the description typed or pasted here, each opened into its
  // descriptions (BabelStone IDS, through `/layers/structure`, with the source regions of each) and
  // those into their parts, level by level, each character's own opened when it is. Every part and
  // every node of a description can be taken whole into the editor's chosen slot, or its first empty
  // one, by a click or by Enter; the arrow keys step through the tree. A sequence the editor cannot
  // read (one naming BabelStone's unencoded component {n}) shows as text, its encoded parts still
  // open. Under each part are the components the variant table puts in its place, and below the tree
  // the characters built from the parts typed, from the component search, each opened here on a click.
  //
  // `known` holds what the structure route answered for each character, `swaps` the substitutes of
  // each component; `lookup(char)` asks for one; `ontake(text)` takes a part or a node, as written.
  import { untrack } from 'svelte'
  import { isNode, write } from '../lib/ids.js'
  import { DEPTH, decomposable, lookupChar, namedParts, roots, searchParts, tokens, treeKey, unencoded, variants } from '../lib/decompose.js'
  import { suggest } from '../lib/layers.js'
  import { t } from '../lib/i18n.svelte.js'
  let { known = {}, swaps = {}, lookup, disabled = false, ontake } = $props()

  const FOUND_MAX = 24
  const COMPONENT_RANK = 7
  const SWAPS_SHOWN = 8
  let text = $state(''), extra = $state([]), toggled = $state(new Set()), focusKey = $state(null)
  let tree = $state(null), input = $state(null)

  const typed = $derived(roots(text))
  // The characters opened from the search's list come first, the last opened at the top.
  const all = $derived([...extra.map(char => ({ kind: 'char', char, browsed: true })), ...typed])

  // The tree as rows in reading order, each with its level, whether it opens and is open, and what
  // taking it puts in the slot. Typed characters start open, as do descriptions and their nodes;
  // a part's own character starts closed. `toggled` holds the rows opened or closed against that.
  const rows = $derived.by(() => {
    const out = []
    const isOpen = (key, byDefault) => byDefault !== toggled.has(key)
    const char = (part, key, level, ancestors, root = false) => {
      const c = lookupChar(part), entry = decomposable(part) ? known[c] : undefined
      const cycle = ancestors.includes(c), deep = ancestors.length >= DEPTH
      const atomic = entry?.status === 'ready' && !variants(entry).length
      const openable = decomposable(part) && !cycle && !deep && !atomic
      const open = openable && isOpen(key, root)
      out.push({ key, level, kind: 'char', part, take: part, openable, open, cycle, deep, atomic })
      if (!open) return
      if (!entry || entry.status === 'loading') out.push({ key: `${key}/loading`, level: level + 1, kind: 'status', status: 'loading', part: c })
      else if (entry.status === 'failed') out.push({ key: `${key}/failed`, level: level + 1, kind: 'status', status: 'failed', part: c })
      else variants(entry).forEach((found, i) => sequence(found, `${key}/${i}`, level + 1, [...ancestors, c]))
    }
    const node = (part, key, level, ancestors) => {
      const open = isOpen(key, true)
      out.push({ key, level, kind: 'node', op: part.op, text: write(part), take: write(part), openable: true, open })
      if (open) part.parts.forEach((inner, i) => isNode(inner) ? node(inner, `${key}.${i}`, level + 1, ancestors) : char(inner, `${key}.${i}`, level + 1, ancestors))
    }
    const sequence = ({ text: written, regions, tree: read }, key, level, ancestors) => {
      if (read) {
        const open = isOpen(key, true)
        out.push({ key, level, kind: 'sequence', op: read.op, text: written, regions, take: written, openable: true, open })
        if (open) read.parts.forEach((inner, i) => isNode(inner) ? node(inner, `${key}.${i}`, level + 1, ancestors) : char(inner, `${key}.${i}`, level + 1, ancestors))
        return
      }
      const named = namedParts(written), open = named.length > 0 && isOpen(key, true)
      out.push({ key, level, kind: 'sequence', text: written, regions, take: null, openable: named.length > 0, open, unreadable: true })
      if (open) named.forEach((part, i) => char(part, `${key}~${i}`, level + 1, ancestors))
    }
    all.forEach((root, i) => root.kind === 'sequence'
      ? sequence({ text: root.text, regions: '', tree: root.tree }, `${i}`, 1, [])
      : char(root.char, root.browsed ? `x${root.char}` : `${i}`, 1, [], true))
    return out
  })

  // A character's descriptions are asked for once it is open.
  $effect(() => {
    for (const row of rows) if (row.kind === 'char' && row.open && !known[lookupChar(row.part)]) untrack(() => lookup(lookupChar(row.part)))
  })

  function typing(event) {
    if (event?.isComposing) return
    text = input.value
    extra = []; toggled = new Set(); focusKey = null
  }

  function toggle(row) {
    if (!row.openable) return
    const next = new Set(toggled)
    if (next.has(row.key)) next.delete(row.key); else next.add(row.key)
    toggled = next
  }
  function take(row) {
    if (disabled) return
    if (row.kind === 'status' && row.status === 'failed') { lookup(row.part, { again: true }); return }
    if (row.take) ontake(row.take)
  }
  function focusRow(key) {
    focusKey = key
    queueMicrotask(() => tree?.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus())
  }
  function rowKey(event, index) {
    if (event.isComposing) return
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); take(rows[index]); return }
    const move = treeKey(rows, index, event.key)
    if (!move) return
    event.preventDefault()
    if (move.toggle !== undefined) toggle(rows[move.toggle])
    else focusRow(rows[move.focus].key)
  }
  // The row that takes the Tab stop: the one last focused while it is shown, else the first.
  const tabStop = $derived(rows.some(row => row.key === focusKey) ? focusKey : rows[0]?.key)

  // The characters built from the parts typed, from the component search, once the typing settles.
  const asked = $derived(searchParts(typed, known))
  let found = $state({ status: 'idle', items: [] })
  $effect(() => {
    const query = asked
    if (!query) { found = { status: 'idle', items: [] }; return }
    const controller = new AbortController()
    found = { status: 'loading', items: [] }
    const timer = setTimeout(() => suggest(query, FOUND_MAX, controller.signal).then(result => {
      // A character the search found by its components is ranked 7, by the site and the local service alike.
      const items = (result?.items ?? []).filter(item => item.rank === COMPONENT_RANK && item.char)
      found = { status: 'ready', items, more: Boolean(result?.more) }
    }, error => { if (!controller.signal.aborted) found = { status: 'failed', items: [], error: error.message } }), 250)
    return () => { clearTimeout(timer); controller.abort() }
  })
  function browse(char) {
    extra = [char, ...extra.filter(other => other !== char)]
    focusRow(`x${char}`)
  }

  // The components the variant table puts in a part's place, those the editor can take (not BabelStone's {n}).
  const swapsOf = part => (swaps[lookupChar(part)] ?? []).filter(item => decomposable(item.char) || item.char === '？').slice(0, SWAPS_SHOWN)
  const regionsTitle = regions => t('form.ids.from.regions', { regions: [...regions].join(' ') })
  const label = row => row.kind === 'char' ? row.part : row.text
</script>

{#snippet written(sequence)}
  {#each tokens(sequence) as token, i (i)}
    {@const n = unencoded(token)}
    {#if n !== null}<abbr class="from-unencoded" title={t('form.ids.from.unencoded', { n })}>{token}</abbr>{:else}{token}{/if}
  {/each}
{/snippet}

<div class="ids-from">
  <label class="from-field">
    <input bind:this={input} aria-label={t('form.ids.pick.from')} value={text} {disabled} lang="ja" spellcheck="false" autocomplete="off"
      placeholder={t('form.ids.from.placeholder')} oninput={typing} oncompositionend={() => typing()}
      onkeydown={event => { if ((event.key === 'ArrowDown' || event.key === 'Enter') && !event.isComposing && rows.length) { event.preventDefault(); focusRow(rows[0].key) } }} />
  </label>
  {#if rows.length}
    <p class="from-hint">{t('form.ids.from.hint')}</p>
    <ul class="from-tree" role="tree" aria-label={t('form.ids.from.tree')} bind:this={tree}>
      {#each rows as row, index (row.key)}
        <li role="treeitem" class="from-row {row.kind}" data-key={row.key} aria-level={row.level} aria-selected="false"
          aria-expanded={row.openable ? row.open : undefined} aria-disabled={!row.take && row.kind !== 'status' ? 'true' : undefined}
          tabindex={row.key === tabStop ? 0 : -1} style:--level={row.level - 1}
          aria-label={row.take ? t('form.ids.from.take', { part: row.take }) : undefined}
          onfocus={() => focusKey = row.key} onkeydown={event => rowKey(event, index)}>
          {#if row.openable}
            <button type="button" class="from-toggle" tabindex="-1" aria-hidden="true"
              title={t(row.open ? 'form.ids.from.collapse' : 'form.ids.from.expand', { part: label(row) })}
              onclick={event => { event.stopPropagation(); toggle(row) }}>{row.open ? '▾' : '▸'}</button>
          {:else}<span class="from-toggle-space"></span>{/if}
          {#if row.kind === 'status'}
            {#if row.status === 'loading'}<span class="from-note" role="status">{t('form.ids.from.loading', { char: row.part })}</span>
            {:else}<span class="from-note fault" role="status">{t('form.ids.from.failed', { char: row.part })}</span>
              <button type="button" class="from-retry" tabindex="-1" {disabled} onclick={() => take(row)}>{t('form.ids.from.retry')}</button>{/if}
          {:else}
            <button type="button" class="from-take" tabindex="-1" disabled={disabled || !row.take} lang="ja"
              title={row.take ? t('form.ids.from.take', { part: row.take }) : t('form.ids.from.unreadable')}
              onclick={() => take(row)}>{#if row.kind === 'char'}{row.part}{:else}{@render written(row.text)}{/if}</button>
            {#if row.regions}<span class="from-regions" title={regionsTitle(row.regions)}>{row.regions}</span>{/if}
            {#if row.unreadable}<span class="from-note">{t('form.ids.from.unreadable')}</span>{/if}
            {#if row.cycle}<span class="from-note">{t('form.ids.from.cycle')}</span>
            {:else if row.deep}<span class="from-note">{t('form.ids.from.deep')}</span>
            {:else if row.atomic && row.level === 1}<span class="from-note">{t('form.ids.from.atomic')}</span>{/if}
            {@const others = swapsOf(row.part)}
            {#if row.kind === 'char' && others.length}
              <span class="from-swaps" title={t('form.ids.swaps', { part: row.part })}>
                <span aria-hidden="true">≈</span>
                {#each others as item (item.char)}
                  <button type="button" class="from-swap" tabindex="-1" {disabled} lang="ja"
                    title={`${row.part} → ${item.char} · ${item.count}`} aria-label={t('form.ids.from.take', { part: item.char })}
                    onclick={event => { event.stopPropagation(); ontake(item.char) }}>{item.char}</button>
                {/each}
              </span>
            {/if}
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
  {#if text.trim()}
    <div class="from-containing">
      {#if asked}
        <span class="from-title">{t('form.ids.from.containing', { parts: [...asked].join(' ') })}</span>
        {#if found.status === 'loading'}<p class="from-note" role="status">{t('form.ids.from.searching')}</p>
        {:else if found.status === 'failed'}<p class="from-note fault" role="status">{t('form.ids.from.searchFailed')}</p>
        {:else if found.status === 'ready' && !found.items.length}<p class="from-note" role="status">{t('form.ids.from.noneContaining')}</p>
        {:else if found.status === 'ready'}
          <div class="from-found">
            {#each found.items as item (item.code_point)}
              <button type="button" class="from-hit" {disabled} lang="ja" title={t('form.ids.from.expand', { part: item.char })}
                onclick={() => browse(item.char)}>{item.char}</button>
            {/each}
          </div>
        {/if}
      {:else}<p class="from-note">{t('form.ids.from.containingHint')}</p>{/if}
    </div>
  {/if}
</div>

<style>
  .ids-from{display:flex;flex-direction:column;gap:6px;min-width:0}
  .from-field{display:flex;flex-direction:column}
  .from-field input{font-size:18px;padding:5px 8px;font-family:var(--ids-font)}
  .from-field input:placeholder-shown{font-size:14px;font-family:inherit;padding:7px 8px}
  .from-title{font-size:11px;color:var(--muted)}
  .from-hint{margin:0;font-size:11px;color:var(--muted)}
  .from-tree{list-style:none;margin:0;padding:4px;display:flex;flex-direction:column;max-height:320px;overflow:auto;
    border:1px solid var(--line);border-radius:8px;background:var(--surface-subtle)}
  .from-row{display:flex;align-items:center;flex-wrap:wrap;gap:4px;padding:1px 4px 1px calc(var(--level) * 18px + 4px);border-radius:5px;min-height:34px}
  .from-row:focus-visible{outline:2px solid var(--focus-ring);outline-offset:-2px;background:var(--accent-light)}
  .from-row:hover{background:var(--surface)}
  .from-toggle,.from-toggle-space{flex:none;width:20px;text-align:center}
  .from-toggle{padding:0;border:0;background:none;color:var(--muted);font-size:12px;line-height:1}
  .from-take{padding:1px 8px;border-color:transparent;background:none;font-family:var(--ids-font);font-size:22px;line-height:1.2;color:var(--ink)}
  .sequence .from-take,.node .from-take{font-size:17px;color:var(--ink-soft)}
  .from-take:not(:disabled):hover{border-color:var(--accent);background:var(--surface)}
  .from-take:disabled{opacity:1;cursor:default;color:var(--muted)}
  .from-unencoded{text-decoration:underline dotted;font-family:ui-monospace,monospace;font-size:12px;color:var(--muted);cursor:help}
  .from-regions{font-size:10px;letter-spacing:.08em;padding:1px 4px;border-radius:4px;background:var(--surface-selected);color:var(--muted);font-variant-numeric:tabular-nums}
  .from-note{margin:0;font-size:11px;color:var(--muted)}
  .from-note.fault{color:var(--fault)}
  .from-retry{padding:1px 8px;font-size:12px}
  .from-swaps{display:inline-flex;flex-wrap:wrap;align-items:center;gap:2px;margin-left:6px;color:var(--muted);font-size:12px}
  .from-swap{min-width:28px;padding:0 4px;font-size:16px;line-height:1.4;font-family:var(--ids-font);border-color:transparent;background:var(--surface)}
  .from-swap:not(:disabled):hover,.from-hit:not(:disabled):hover{border-color:var(--accent)}
  .from-containing{display:flex;flex-direction:column;gap:4px}
  .from-found{display:grid;grid-template-columns:repeat(auto-fill,minmax(38px,1fr));gap:3px;max-height:160px;overflow:auto}
  .from-hit{aspect-ratio:1;padding:0;font-size:22px;line-height:1;font-family:var(--ids-font);border-color:transparent;background:var(--surface-subtle)}
</style>
