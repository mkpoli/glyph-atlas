<script>
  // The collection's graphemes, one tile each, with the forms a tile gathers shown in a popover: 仮
  // and 假 are one tile, and pointing at it lists both with their own code points and counts. Choosing
  // the tile narrows the grid to the whole family; choosing a form in the popover opens that form's
  // own gallery. Each group is `{ key, char, count, members: [{ label, count }] }`.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { number } from '../lib/client.js'
  import { t } from '../lib/i18n.svelte.js'

  let { groups = [], value = '', onchoose = () => {}, onform = () => {} } = $props()
  let shown = $state(null), place = $state({ left: 0, top: 0 }), timer
  const codes = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')

  function show(group, event) {
    clearTimeout(timer)
    if (group.members.length < 2 && group.members[0]?.label === group.char) { shown = null; return }
    const rect = event.currentTarget.getBoundingClientRect()
    const width = 240
    const left = rect.right + 8 + width <= innerWidth ? rect.right + 8 : Math.max(8, rect.left - 8 - width)
    place = { left, top: Math.min(rect.top, innerHeight - 60) }
    shown = group
  }
  // The popover stays while the pointer crosses the gap from the tile to it.
  function hide() { clearTimeout(timer); timer = setTimeout(() => shown = null, 160) }
</script>

<div class="category-options grapheme-grid">
  {#each groups as group (group.key)}
    <button type="button" class:chosen={value === group.key} onclick={() => { shown = null; onchoose(group.key) }}
            onpointerenter={e => show(group, e)} onpointerleave={hide} onfocus={e => show(group, e)} onblur={hide}
            aria-label={group.members.length > 1 ? `${group.char} · ${t('explore.grapheme.forms', { count: group.members.length })}` : undefined}>
      <ReferenceGlyph char={group.char} size="md" />
      <small>{number(group.count)}</small>
      {#if group.members.length > 1}<i class="form-count" aria-hidden="true">{group.members.length}</i>{/if}
    </button>
  {/each}
</div>

{#if shown}
  <div class="grapheme-popover" role="tooltip" style="left:{place.left}px;top:{place.top}px"
       onpointerenter={() => clearTimeout(timer)} onpointerleave={hide}>
    <div class="popover-head">
      <ReferenceGlyph char={shown.char} size="lg" />
      <span><b>{t('chips.grapheme')}</b> <code>{shown.key}</code><br />{t('explore.grapheme.forms', { count: shown.members.length })} · {number(shown.count)}</span>
    </div>
    <ul>
      {#each shown.members as member (member.label)}
        <li><button type="button" tabindex="-1" onclick={() => { const form = member.label; shown = null; onform(form) }}>
          <ReferenceGlyph char={member.label} size="md" /><code>{codes(member.label)}</code><small>{number(member.count)}</small>
        </button></li>
      {/each}
    </ul>
    <button type="button" class="popover-all" tabindex="-1" onclick={() => { const key = shown.key; shown = null; onchoose(key) }}>{t('chips.allForms')}</button>
  </div>
{/if}

<style>
  .grapheme-grid button{position:relative}
  .form-count{position:absolute;top:4px;right:5px;font-style:normal;font-size:9px;line-height:1;color:var(--muted)}
  .grapheme-popover{position:fixed;z-index:60;width:240px;padding:10px;border:1px solid var(--line);border-radius:9px;background:#fff;box-shadow:0 14px 40px #0002;font-size:12px}
  .popover-head{display:flex;gap:10px;align-items:center;padding:2px 4px 8px;border-bottom:1px solid var(--line);color:var(--muted);line-height:1.5}
  .popover-head b{font-weight:500;color:var(--ink)}
  .grapheme-popover ul{list-style:none;margin:6px 0 0;padding:0;max-height:260px;overflow:auto}
  .grapheme-popover li button{display:flex;align-items:center;gap:10px;width:100%;border:0;border-radius:5px;background:transparent;padding:5px 6px;text-align:left}
  .grapheme-popover li button:hover{background:var(--accent-light);color:var(--accent)}
  .grapheme-popover code{font-family:ui-monospace,monospace;font-size:10px;color:var(--muted)}
  .grapheme-popover li small{margin-left:auto;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .popover-all{width:100%;margin-top:6px;border:0;border-top:1px solid var(--line);border-radius:0;background:transparent;padding:8px 6px 2px;text-align:left;font-size:12px;color:var(--accent)}
</style>
