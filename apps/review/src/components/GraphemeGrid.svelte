<script>
  // The collection's graphemes, one tile each, with the forms a tile gathers shown in a popover: 仮
  // and 假 are one tile, and pointing at it lists both with their own code points and counts. Choosing
  // the tile narrows the grid to the whole family; choosing a form in the popover opens that form's
  // own gallery. Each group is `{ key, char, count, members: [{ label, count }] }`.
  //
  // The popover follows the tile's focus as well as the pointer, and sits after its tile in the
  // document, so Tab from a tile walks into its forms. On a touch screen the first tap on a tile with
  // forms to list opens the popover, whose "All forms" chooses the family.
  import { tick } from 'svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { number } from '../lib/client.js'
  import { t } from '../lib/i18n.svelte.js'

  let { groups = [], value = '', onchoose = () => {}, onform = () => {} } = $props()
  let shown = $state(null), place = $state({ left: 0, top: 0 }), card = $state(null), timer, touched = false, opened = false, quiet = false
  const codes = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
  // A tile of one form that is its own grapheme has nothing more to show.
  const listed = group => group.members.length > 1 || group.members[0]?.label !== group.char

  async function show(group, tile) {
    clearTimeout(timer)
    if (!listed(group)) { shown = null; return }
    shown = group.key
    const rect = tile.getBoundingClientRect(), width = 240
    const left = rect.right + 8 + width <= innerWidth ? rect.right + 8 : Math.max(8, rect.left - 8 - width)
    place = { left, top: rect.top }
    await tick()
    // Kept whole on screen: a card taller than the room below the tile moves up.
    const height = card?.offsetHeight ?? 0
    place = { left, top: Math.max(8, Math.min(rect.top, innerHeight - height - 8)) }
  }
  function hide() { clearTimeout(timer); timer = setTimeout(() => shown = null, 160) }
  function close() { clearTimeout(timer); shown = null }

  function press(group, tile) {
    // A first tap opens the card; the next one, or "All forms", chooses.
    if (touched && listed(group) && !opened) { show(group, tile); return }
    close()
    onchoose(group.key)
  }
  // Focus leaving a tile and its card together closes the card; moving between them does not.
  function left(event) {
    if (!event.currentTarget.contains(event.relatedTarget)) hide()
  }
</script>

<svelte:window onscrollcapture={e => { if (!card?.contains(e.target)) close() }} onresize={close} />

<div class="category-options grapheme-grid">
  {#each groups as group (group.key)}
    <div class="grapheme-cell" onfocusout={left}
         onkeydown={e => { if (e.key === 'Escape' && shown === group.key) { e.preventDefault(); e.stopPropagation(); close(); quiet = true; e.currentTarget.querySelector('.grapheme-tile')?.focus(); quiet = false } }}>
      <button type="button" class="grapheme-tile" class:chosen={value === group.key}
              onpointerdown={e => { touched = e.pointerType === 'touch'; opened = shown === group.key }}
              onpointerenter={e => { if (e.pointerType !== 'touch') show(group, e.currentTarget) }}
              onpointerleave={e => { if (e.pointerType !== 'touch') hide() }}
              onfocus={e => { if (!quiet) show(group, e.currentTarget) }} onclick={e => press(group, e.currentTarget)}
              aria-expanded={listed(group) ? shown === group.key : undefined}
              aria-label={group.members.length > 1 ? `${group.char} · ${t('explore.grapheme.forms', { count: group.members.length })}` : undefined}>
        <ReferenceGlyph char={group.char} size="md" />
        <small>{number(group.count)}</small>
        {#if group.members.length > 1}<i class="form-count" aria-hidden="true">{group.members.length}</i>{/if}
      </button>
      {#if shown === group.key}
        <div class="grapheme-popover" bind:this={card} style="left:{place.left}px;top:{place.top}px"
             onpointerenter={() => clearTimeout(timer)} onpointerleave={e => { if (e.pointerType !== 'touch') hide() }}>
          <div class="popover-head">
            <ReferenceGlyph char={group.char} size="lg" />
            <span><b>{t('chips.grapheme')}</b> <code>{group.key}</code><br />{t('explore.grapheme.forms', { count: group.members.length })} · {number(group.count)}</span>
          </div>
          <ul>
            {#each group.members as member (member.label)}
              <li><button type="button" onclick={() => { close(); onform(member.label) }}>
                <ReferenceGlyph char={member.label} size="md" /><code>{codes(member.label)}</code><small>{number(member.count)}</small>
              </button></li>
            {/each}
          </ul>
          <button type="button" class="popover-all" onclick={() => { close(); onchoose(group.key) }}>{t('chips.allForms')}</button>
        </div>
      {/if}
    </div>
  {/each}
</div>

<style>
  .grapheme-cell{display:contents}
  .grapheme-tile{position:relative}
  .form-count{position:absolute;top:4px;right:5px;font-style:normal;font-size:9px;line-height:1;color:var(--muted)}
  .grapheme-popover{position:fixed;z-index:60;width:240px;padding:10px;border:1px solid var(--line);border-radius:9px;background:var(--surface);box-shadow:0 14px 40px var(--shadow);font-size:12px}
  .popover-head{display:flex;gap:10px;align-items:center;padding:2px 4px 8px;border-bottom:1px solid var(--line);color:var(--muted);line-height:1.5}
  .popover-head b{font-weight:500;color:var(--ink)}
  .popover-head>span{font-size:12px}
  .grapheme-popover ul{list-style:none;margin:6px 0 0;padding:0;max-height:260px;overflow:auto}
  .grapheme-popover li button{display:flex;flex-direction:row;align-items:center;justify-content:flex-start;gap:10px;width:100%;min-height:0;border:0;border-radius:5px;background:transparent;padding:5px 6px;text-align:left}
  .grapheme-popover li button:hover,.grapheme-popover li button:focus-visible{background:var(--accent-light);color:var(--accent)}
  .grapheme-popover code{font-family:ui-monospace,monospace;font-size:10px;color:var(--muted)}
  .grapheme-popover li small{margin-left:auto;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .popover-all{display:block;min-height:0;width:100%;margin-top:6px;border:0;border-top:1px solid var(--line);border-radius:0;background:transparent;padding:8px 6px 2px;text-align:left;font-size:12px;color:var(--accent)}
</style>
