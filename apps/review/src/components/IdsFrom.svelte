<script>
  // "Take parts from": the characters or the description typed or pasted here, each opened into its
  // descriptions (BabelStone IDS, through `/layers/structure`, with the source regions of each) and
  // those into their parts, level by level, each character's own opened when it is. Every part and
  // every node of a description can be taken whole into the editor's chosen slot, or its first empty
  // one, by a click or by Enter; the arrow keys step through the tree, and ArrowRight on a part steps
  // through the components the variant table puts in its place, shown beside it. Below the tree are
  // the characters built from the parts typed, from the component search, each opened here on a click.
  // The structure route drops sequences naming BabelStone's unencoded components ({n}); one that
  // still reaches the box would show as text, not taken, its encoded parts still open.
  //
  // The tree's rows are its only controls: the toggle and the swaps beside a part are drawn inside
  // its row and reached by the row's keys; failures and loaded parts are said in one live region.
  //
  // `known` holds what the structure route answered for each character, `swaps` the substitutes of
  // each component; `lookup(char)` asks for one; `ontake(text)` takes a part or a node, as written.
  import { untrack } from 'svelte'
  import { isNode, leaves, write } from '../lib/ids.js'
  import { DEPTH, ROOTS, decomposable, lookupChar, namedParts, roots, searchParts, tokens, treeKey, unencoded, variants } from '../lib/decompose.js'
  import { suggest } from '../lib/layers.js'
  import { base, t } from '../lib/i18n.svelte.js'
  let { known = {}, swaps = {}, lookup, disabled = false, ontake } = $props()

  const FOUND_MAX = 24
  const SWAPS_SHOWN = 8
  // A character the search found by its components is ranked 7, by the site and the local service alike.
  const COMPONENT_RANK = 7
  // The IRG source letters BabelStone tags a regional shape with (`refs.SOURCE_REGIONS`), each with the
  // region its kIRG_*Source field of UAX #38, Unicode Han Database (Unihan), names
  // (https://www.unicode.org/reports/tr38/): G China, H Hong Kong, M Macao, T Taiwan, J Japan, K the
  // Republic of Korea, P the Democratic People's Republic of Korea, V Vietnam, B the United Kingdom,
  // named in the reader's language by the browser's region names. U (the UTC) and S (the SAT
  // Daizōkyō Text Database) are no regions and keep their own names.
  const REGIONS = { G: 'CN', H: 'HK', M: 'MO', T: 'TW', J: 'JP', K: 'KR', P: 'KP', V: 'VN', B: 'GB', U: 'UTC', S: 'SAT' }
  const displayNames = $derived.by(() => { try { return new Intl.DisplayNames([base()], { type: 'region' }) } catch { return null } })

  let text = $state(''), extra = $state([]), toggled = $state(new Set())
  let focusKey = $state(null), swapAt = $state(null), live = $state('')
  let tree = $state(null), input = $state(null)

  const typed = $derived(roots(text))
  // The characters opened from the search's list come first, the last opened at the top, keyed apart
  // from those typed so opening one leaves the typed trees as they are.
  const all = $derived([...extra.map(char => ({ kind: 'char', char, key: `x${char}` })), ...typed.map((root, i) => ({ ...root, key: `t${i}` }))])
  const swapsOf = part => (swaps[lookupChar(part)] ?? []).filter(item => decomposable(item.char) || item.char === '？').slice(0, SWAPS_SHOWN)

  // The tree as rows in reading order, each with its level, whether it opens and is open, what taking
  // it puts in the slot and the swaps beside it. Typed characters start open, as do descriptions and
  // their nodes; a part's own character starts closed. `toggled` holds the rows opened or closed
  // against that.
  const rows = $derived.by(() => {
    const out = []
    const isOpen = (key, byDefault) => byDefault !== toggled.has(key)
    const char = (part, key, level, ancestors, root = false) => {
      const c = lookupChar(part), entry = decomposable(part) ? known[c] : undefined
      const repeats = ancestors.includes(c), deep = !repeats && ancestors.length >= DEPTH
      const atomic = entry?.status === 'ready' && !variants(entry).length
      const openable = decomposable(part) && !repeats && !deep && !atomic
      const open = openable && isOpen(key, root)
      const note = repeats ? 'cycle' : deep ? 'deep' : atomic ? 'atomic' : null
      out.push({ key, level, kind: 'char', part, take: part, openable, open, note, swaps: swapsOf(part) })
      if (!open) return
      if (!entry || entry.status === 'loading') out.push({ key: `${key}/loading`, level: level + 1, kind: 'status', status: 'loading', part: c })
      else if (entry.status === 'failed') out.push({ key: `${key}/failed`, level: level + 1, kind: 'status', status: 'failed', part: c })
      else variants(entry).forEach((found, i) => sequence(found, `${key}/${i}`, level + 1, [...ancestors, c]))
    }
    const parts = (node, key, level, ancestors) => node.parts.forEach((inner, i) => isNode(inner)
      ? nodeRow(inner, `${key}.${i}`, level, ancestors) : char(inner, `${key}.${i}`, level, ancestors))
    const nodeRow = (part, key, level, ancestors) => {
      const open = isOpen(key, true)
      out.push({ key, level, kind: 'node', text: write(part), take: write(part), openable: true, open, swaps: [] })
      if (open) parts(part, key, level + 1, ancestors)
    }
    const sequence = ({ text: written, regions, tree: read }, key, level, ancestors) => {
      if (read) {
        const open = isOpen(key, true)
        out.push({ key, level, kind: 'sequence', text: written, regions, take: written, openable: true, open, swaps: [] })
        if (open) parts(read, key, level + 1, ancestors)
        return
      }
      const named = namedParts(written), open = named.length > 0 && isOpen(key, true)
      out.push({ key, level, kind: 'sequence', text: written, regions, take: null, openable: named.length > 0, open, note: 'unreadable', swaps: [] })
      if (open) named.forEach((part, i) => char(part, `${key}~${i}`, level + 1, ancestors))
    }
    for (const root of all) root.kind === 'sequence'
      ? sequence({ text: root.text, regions: '', tree: root.tree }, root.key, 1, [])
      : char(root.char, root.key, 1, [], true)
    return out
  })

  // A character's descriptions are asked for once it is open.
  $effect(() => {
    for (const row of rows) if (row.kind === 'char' && row.open && !known[lookupChar(row.part)]) untrack(() => lookup(lookupChar(row.part)))
  })
  // A loading or failed row that has the focus when its answer comes hands the focus on: to the first
  // row that took its place, else to its character's row. Whether it had the focus is read before the
  // rows are redrawn; focus anywhere else is left where it is.
  let handOn = null
  $effect.pre(() => {
    rows
    untrack(() => {
      const key = document.activeElement?.dataset?.key
      handOn = key && tree?.contains(document.activeElement) && /\/(loading|failed)$/.test(key) ? key : null
    })
  })
  $effect(() => {
    // Read the rows first, so the effect runs again after each redraw the $effect.pre saw.
    const current = rows
    const key = handOn
    if (!key || current.some(row => row.key === key)) return
    handOn = null
    const base = key.replace(/\/(loading|failed)$/, '')
    const next = current.find(row => row.key.startsWith(`${base}/`)) ?? current.find(row => row.key === base)
    if (next) focusRow(next.key)
  })
  // The live region says when a character's descriptions fail, each time, and how many parts a row
  // the reader opened came to once they load. Loading itself is not said.
  const seen = new Map()
  function announce(message) {
    if (live !== message) { live = message; return }
    live = ''
    queueMicrotask(() => { live = message })
  }
  $effect(() => {
    for (const row of rows) {
      if (row.kind !== 'char' || !(row.open || row.note === 'atomic')) continue
      const c = lookupChar(row.part), status = known[c]?.status, was = seen.get(row.key)
      if (status === was) continue
      seen.set(row.key, status)
      untrack(() => {
        if (status === 'failed') announce(statusText({ status, part: c }))
        else if (status === 'ready' && was === 'loading' && (toggled.has(row.key) || row.key.startsWith('x'))) {
          const first = variants(known[c]).find(found => found.tree)?.tree
          announce(row.note === 'atomic' || !first ? `${c}: ${t('form.ids.from.atomic')}` : t('form.ids.from.partsLoaded', { char: c, count: leaves(first).length }))
        }
      })
    }
  })

  function typing(event) {
    if (event?.isComposing) return
    text = input.value
    extra = []; toggled = new Set(); focusKey = null; swapAt = null; seen.clear()
  }

  function toggle(row) {
    if (!row.openable) return
    const next = new Set(toggled)
    if (next.has(row.key)) next.delete(row.key); else next.add(row.key)
    toggled = next
  }
  function take(row, swap = null) {
    if (disabled) return
    if (row.kind === 'status') { if (row.status === 'failed') lookup(row.part, { again: true }); return }
    const part = swap === null ? row.take : row.swaps[swap]?.char
    if (part) ontake(part)
  }
  function focusRow(key) {
    if (key !== focusKey) swapAt = null
    focusKey = key
    queueMicrotask(() => tree?.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus())
  }
  function rowKey(event, index) {
    if (event.isComposing || event.target !== event.currentTarget) return
    const row = rows[index]
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); take(row, swapAt); return }
    const move = treeKey(rows, index, event.key, swapAt)
    if (!move) return
    event.preventDefault()
    if (move.toggle !== undefined) toggle(rows[move.toggle])
    else if (move.swap !== undefined) {
      swapAt = move.swap
      if (swapAt !== null) live = `${row.swaps[swapAt].char}, ${t('form.ids.swaps', { part: row.part })}`
    } else if (move.focus !== index) focusRow(rows[move.focus].key)
  }
  // A click on the row takes it; on its toggle, opens or closes it; on a swap beside it, takes that.
  function rowClick(event, row) {
    focusKey = row.key
    const swap = event.target.closest?.('[data-swap]')
    if (swap) { swapAt = Number(swap.dataset.swap); take(row, swapAt); return }
    swapAt = null
    if (event.target.closest?.('.from-toggle')) toggle(row)
    else take(row)
  }
  // The row that takes the Tab stop: the one last focused while it is shown, else the first.
  const tabStop = $derived(rows.some(row => row.key === focusKey) ? focusKey : rows[0]?.key)

  const regionName = letter => REGIONS[letter].length === 2 ? displayNames?.of(REGIONS[letter]) ?? REGIONS[letter] : REGIONS[letter]
  const regionNames = regions => [...regions].filter(r => r in REGIONS).map(r => `${r} ${regionName(r)}`).join(' · ')
  const regionsTitle = regions => t('form.ids.from.regions', { regions: regionNames(regions) })
  // What a row is read out as: what taking it puts in the slot, its regions, and its note.
  const rowLabel = row => [
    row.kind === 'status' ? statusText(row)
      : row.take ? t('form.ids.from.take', { part: row.take }) : row.text,
    row.regions ? regionsTitle(row.regions) : null,
    row.note ? t(`form.ids.from.${row.note}`) : null,
    row.status === 'failed' ? t('form.ids.from.retry') : null,
    row.swaps?.length ? t('form.ids.from.swapsHint') : null,
  ].filter(Boolean).join('. ')
  const label = row => row.kind === 'char' ? row.part : row.text
  function statusText(row) {
    const key = row.status === 'failed' ? 'form.ids.from.failed' : 'form.ids.from.loading'
    return t(key, { char: row.part })
  }

  // The characters built from the parts typed, from the component search, once the typing settles.
  // The search answers a name or a reading the parts spell before it looks for them as components.
  const asked = $derived(searchParts(typed, known))
  let found = $state({ status: 'idle', items: [] })
  $effect(() => {
    const query = asked
    if (!query) { found = { status: 'idle', items: [] }; return }
    const controller = new AbortController()
    found = { status: 'loading', items: [] }
    const timer = setTimeout(() => suggest(query, FOUND_MAX, controller.signal).then(result => {
      if (controller.signal.aborted) return
      const all = result?.items ?? []
      const items = all.filter(item => item.rank === COMPONENT_RANK && item.char)
      found = { status: !items.length && all.length ? 'named' : 'ready', items, more: Boolean(result?.more) && items.length > 0 }
    }, () => { if (!controller.signal.aborted) found = { status: 'failed', items: [] } }), 250)
    return () => { clearTimeout(timer); controller.abort() }
  })
  function browse(char) {
    extra = [char, ...extra.filter(other => other !== char)].slice(0, ROOTS)
    focusRow(`x${char}`)
  }
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
  <p class="from-live" aria-live="polite">{live}</p>
  {#if rows.length}
    <p class="from-hint">{t('form.ids.from.hint')}</p>
    <ul class="from-tree" role="tree" aria-label={t('form.ids.from.tree')} bind:this={tree}>
      {#each rows as row, index (row.key)}
        {@const current = row.key === focusKey ? swapAt : null}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <li role="treeitem" class="from-row {row.kind}" class:off={!row.take && row.kind !== 'status'} data-key={row.key}
          aria-level={row.level} aria-selected="false" aria-expanded={row.openable ? row.open : undefined}
          aria-disabled={disabled || (!row.take && row.status !== 'failed') ? 'true' : undefined} aria-label={rowLabel(row)}
          tabindex={row.key === tabStop ? 0 : -1} style:--level={row.level - 1}
          onfocus={() => { if (focusKey !== row.key) swapAt = null; focusKey = row.key }}
          onkeydown={event => rowKey(event, index)} onclick={event => rowClick(event, row)}>
          <span class="from-toggle" aria-hidden="true" title={row.openable ? t(row.open ? 'form.ids.from.collapse' : 'form.ids.from.expand', { part: label(row) }) : undefined}>{row.openable ? row.open ? '▾' : '▸' : ''}</span>
          {#if row.kind === 'status'}
            <span class="from-note" class:fault={row.status === 'failed'} aria-hidden="true">{statusText(row)}</span>
            {#if row.status === 'failed'}<span class="from-retry" aria-hidden="true">{t('form.ids.from.retry')}</span>{/if}
          {:else}
            <span class="from-take" lang="ja" aria-hidden="true">{#if row.kind === 'char'}{row.part}{:else}{@render written(row.text)}{/if}</span>
            {#if row.regions}<span class="from-regions" aria-hidden="true" title={regionsTitle(row.regions)}>{row.regions}</span>{/if}
            {#if row.note && (row.note !== 'atomic' || row.level === 1)}<span class="from-note" aria-hidden="true">{t(`form.ids.from.${row.note}`)}</span>{/if}
            {#if row.swaps.length}
              <span class="from-swaps" aria-hidden="true" title={t('form.ids.swaps', { part: row.part })}>
                <span class="from-approx">≈</span>
                {#each row.swaps as item, k (item.char)}
                  <span class="from-swap" class:active={current === k} data-swap={k} lang="ja" title={`${row.part} → ${item.char} · ${item.count}`}>{item.char}</span>
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
        {#if found.status === 'loading'}<p class="from-note">{t('form.ids.from.searching')}</p>
        {:else if found.status === 'failed'}<p class="from-note fault">{t('form.ids.from.searchFailed')}</p>
        {:else if found.status === 'named'}<p class="from-note">{t('form.ids.from.containingNamed', { parts: asked })}</p>
        {:else if found.status === 'ready' && !found.items.length}<p class="from-note">{t('form.ids.from.noneContaining')}</p>
        {:else if found.status === 'ready'}
          <div class="from-found">
            {#each found.items as item (item.code_point)}
              <button type="button" class="from-hit" {disabled} lang="ja" title={t('form.ids.from.expand', { part: item.char })}
                onclick={() => browse(item.char)}>{item.char}</button>
            {/each}
          </div>
          {#if found.more}<p class="from-note">{t('form.ids.from.more')}</p>{/if}
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
  .from-live{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;margin:0}
  .from-title{font-size:11px;color:var(--muted)}
  .from-hint{margin:0;font-size:11px;color:var(--muted)}
  .from-tree{list-style:none;margin:0;padding:4px;display:flex;flex-direction:column;max-height:320px;overflow:auto;
    border:1px solid var(--line);border-radius:8px;background:var(--surface-subtle)}
  .from-row{display:flex;align-items:center;flex-wrap:wrap;gap:4px;padding:1px 4px 1px calc(var(--level) * 18px + 4px);border-radius:5px;min-height:34px;cursor:pointer}
  .from-row.off{cursor:default}
  .from-row:focus-visible{outline:2px solid var(--focus-ring);outline-offset:-2px;background:var(--accent-light)}
  .from-row:hover{background:var(--surface)}
  .from-toggle{flex:none;width:20px;text-align:center;color:var(--muted);font-size:12px;line-height:1}
  .from-take{padding:1px 8px;border:1px solid transparent;border-radius:5px;font-family:var(--ids-font);font-size:22px;line-height:1.2;color:var(--ink)}
  .sequence .from-take,.node .from-take{font-size:17px;color:var(--ink-soft)}
  .from-row:not(.off):hover .from-take{border-color:var(--accent);background:var(--surface)}
  .off .from-take{color:var(--muted)}
  .from-unencoded{text-decoration:underline dotted;font-family:ui-monospace,monospace;font-size:12px;color:var(--muted);cursor:help}
  .from-regions{font-size:10px;letter-spacing:.08em;padding:1px 4px;border-radius:4px;background:var(--surface-selected);color:var(--muted);font-variant-numeric:tabular-nums;cursor:help}
  .from-note{margin:0;font-size:11px;color:var(--muted)}
  .from-note.fault{color:var(--fault)}
  .from-retry{padding:1px 8px;font-size:12px;border:1px solid var(--line);border-radius:5px;background:var(--surface)}
  .from-swaps{display:inline-flex;flex-wrap:wrap;align-items:center;gap:2px;margin-left:6px;color:var(--muted);font-size:12px}
  .from-approx{margin-right:2px}
  .from-swap{min-width:28px;padding:0 4px;text-align:center;font-size:16px;line-height:1.4;font-family:var(--ids-font);border:1px solid transparent;border-radius:5px;background:var(--surface);color:var(--ink-soft)}
  .from-swap:hover,.from-swap.active,.from-hit:not(:disabled):hover{border-color:var(--accent)}
  .from-swap.active{background:var(--accent-light);color:var(--ink)}
  .from-containing{display:flex;flex-direction:column;gap:4px}
  .from-found{display:grid;grid-template-columns:repeat(auto-fill,minmax(38px,1fr));gap:3px;max-height:160px;overflow:auto}
  .from-hit{aspect-ratio:1;padding:0;font-size:22px;line-height:1;font-family:var(--ids-font);border-color:transparent;background:var(--surface-subtle)}
</style>
