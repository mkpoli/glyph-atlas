<script>
  // What an IDS chip opens (`lib/idsCard.js`): its description as text, coloured by layer with a line
  // under what each operator takes, a button that copies it, and the same layers as boxes laid out as
  // the operators place their parts. Pointing at a part marks it in both. `stay` and `leave` keep the
  // card while the pointer or the focus is in it.
  import { onMount } from 'svelte'
  import CopyValue from './CopyValue.svelte'
  import OperatorIcon from './OperatorIcon.svelte'
  import ScriptText from './ScriptText.svelte'
  import { OP_NAMES, isNode, write } from '../lib/ids.js'
  import { unencoded } from '../lib/decompose.js'
  import { boxLayout, boxSize, height, readSequence, tint } from '../lib/idsView.js'
  import { placeBeside } from '../lib/popoverPlace.js'
  import { t } from '../lib/i18n.svelte.js'
  let { text, lang = '', side = false, trigger, stay, leave, hide } = $props()
  const tree = $derived(readSequence(text))
  const size = $derived(boxSize(tree))
  // A part's side in pixels: smaller the more parts stand beside or over each other.
  const unit = $derived(Math.max(24, Math.min(46, 300 / size.width, 240 / size.height)))
  const key = path => path.join('.')
  let root, hovered = $state(null)
  const within = path => hovered !== null && (hovered === '' || key(path) === hovered || key(path).startsWith(hovered + '.'))
  export const element = () => root
  export function enter() { root.querySelector('.copy-value')?.focus() }

  const place = () => {
    const at = trigger.getBoundingClientRect()
    // What it describes has gone, or been scrolled away.
    if (!trigger.isConnected || !at.width || at.bottom < 0 || at.top > innerHeight) hide()
    else placeBeside(root, trigger, { side })
  }
  function keydown(event) {
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation()
      if (root.contains(document.activeElement)) trigger.focus({ preventScroll: true })
      hide()
    } else if (event.key === 'Tab' && root.contains(document.activeElement)) {
      // Back to what it describes, or on from there.
      if (event.shiftKey) event.preventDefault()
      trigger.focus({ preventScroll: true })
      if (!event.shiftKey) hide()
    }
  }
  onMount(() => {
    root.showPopover()
    place()
    const stop = new AbortController(), options = { signal: stop.signal, capture: true }
    document.addEventListener('keydown', keydown, options)
    document.addEventListener('pointerdown', event => { if (!root.contains(event.target) && !trigger.contains(event.target)) hide() }, options)
    window.addEventListener('resize', place, options)
    window.addEventListener('scroll', event => { if (!root.contains(event.target)) place() }, options)
    return () => stop.abort()
  })
</script>

{#snippet glyph(part)}
  {@const n = unencoded(part)}
  {#if n !== null}<span class="unencoded" title={t('form.ids.from.unencoded', { n })}>{part}</span>{:else}<ScriptText text={part} script="han" {lang} titled={false} />{/if}
{/snippet}

{#snippet run(part, path)}
  {#if isNode(part)}<span class="scope" style:--tint={tint(path, true)} style:--lane={height(part)}><span class="mark" class:hovered={within(path)} data-path={key(path)} title={t(`form.ids.op.${OP_NAMES[part.op]}`)}>{part.op}</span>{#each part.parts as inner, i (i)}{@render run(inner, [...path, i])}{/each}</span>{:else}<span class="mark" class:hovered={within(path)} data-path={key(path)}>{@render glyph(part)}</span>{/if}
{/snippet}

{#snippet box(part, path)}
  {#if isNode(part)}
    <div class="node {boxLayout(part)}" class:hovered={key(path) === hovered} data-op={part.op} data-path={key(path)} style:--tint={tint(path, true)}>
      <div class="head" title={t(`form.ids.op.${OP_NAMES[part.op]}`)}><OperatorIcon op={part.op} size={13} />{#if path.length}<span>{write(part)}</span>{/if}</div>
      <div class="parts">
        {#each part.parts as inner, i (i)}
          <div class="cell" data-index={i} style:flex-grow={boxSize(inner)[boxLayout(part) === 'row' ? 'width' : 'height']}>{@render box(inner, [...path, i])}</div>
        {/each}
      </div>
    </div>
  {:else}
    <div class="part" class:hovered={key(path) === hovered} data-path={key(path)} style:--tint={tint(path)}>{@render glyph(part)}</div>
  {/if}
{/snippet}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ids-card" popover="manual" role="group" aria-label={t('form.ids.label')} bind:this={root}
  onpointerenter={stay} onpointerleave={event => { hovered = null; if (event.pointerType !== 'touch') leave() }} onfocusin={stay} onfocusout={leave}
  onpointermove={event => { hovered = event.target.closest('[data-path]')?.dataset.path ?? null }}>
  <div class="line">
    <span class="sequence" role="img" aria-label={text} lang={lang || 'ja'} style:--lanes={height(tree)}>{@render run(tree, [])}</span>
    <CopyValue value={text} />
  </div>
  <div class="boxes" aria-hidden="true" lang={lang || 'ja'} style:--unit="{unit}px">{@render box(tree, [])}</div>
</div>

<style>
  .ids-card{inset:auto;margin:0;width:max-content;max-width:min(360px,calc(100vw - 16px));padding:12px;border:1px solid var(--line);border-radius:10px;
    background:var(--surface);color:var(--ink);box-shadow:0 6px 24px var(--shadow);font-size:16px;font-weight:400;line-height:1.4;letter-spacing:normal;text-align:left;
    white-space:normal;cursor:default;overflow:auto;overscroll-behavior:contain;user-select:text;-webkit-user-select:text}
  .ids-card:popover-open{position:fixed;display:flex;flex-direction:column;gap:10px}
  .ids-card :global(.script-char[data-script]){color:inherit}
  .line{display:flex;align-items:flex-start;gap:8px}
  .line :global(.copy-value){opacity:1;margin:1px -4px 0 auto}
  /* Each operator draws a line under what it takes, the outer lines lower; a long run wraps. */
  .sequence{min-width:0;font-family:var(--char-font);font-size:20px;line-height:calc(1.4em + var(--lanes) * 8px);line-break:anywhere;overflow-wrap:anywhere;margin-top:calc(var(--lanes) * -4px)}
  .scope{padding-bottom:calc(1px + var(--lane) * 4px);border-bottom:1.5px solid color-mix(in oklab,var(--tint) 60%,var(--ink-soft))}
  .mark{padding:1px 0;color:color-mix(in oklab,var(--ink) 76%,var(--tint));background:color-mix(in oklab,var(--tint) 14%,transparent)}
  .mark.hovered{background:color-mix(in oklab,var(--tint) 44%,transparent)}
  .unencoded{font-family:ui-monospace,monospace;font-size:.6em}

  .boxes{--leaf:calc(var(--unit) * .6);display:grid;min-width:132px;overflow:auto}
  .node{display:flex;flex-direction:column;height:100%;min-width:0;padding:3px 5px 5px;border:1.5px dashed color-mix(in oklab,var(--tint) 55%,var(--line-strong));border-radius:8px;
    background:color-mix(in oklab,var(--tint) 10%,var(--surface))}
  .head{display:flex;align-items:center;gap:5px;min-width:0;margin-bottom:3px;font:11px/1.5 var(--char-font);color:var(--muted)}
  /* The boxes give a node its width; its own sequence takes what they leave. */
  .head span{flex:1 1 0;width:0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .parts{flex:1;display:flex;gap:5px;min-width:0}
  .column > .parts{flex-direction:column}
  .cell{flex:1 1 auto;display:grid;min-width:0}
  .part{display:grid;place-items:center;min-width:var(--unit);min-height:var(--unit);padding:2px 4px;border:1px solid color-mix(in oklab,var(--tint) 45%,var(--line));border-radius:6px;
    font-family:var(--char-font);font-size:var(--leaf);line-height:1.2;background:color-mix(in oklab,var(--tint) 12%,var(--surface));color:color-mix(in oklab,var(--ink) 80%,var(--tint))}
  .node.hovered,.part.hovered{border-color:var(--accent);border-style:solid}
  .part.hovered{background:color-mix(in oklab,var(--tint) 34%,var(--surface))}
  /* An enclosure: the first part fills the box, its glyph on the closed side, and the second sits
     inside it where the operator puts it. */
  .enclosure > .parts{display:grid;grid-template-columns:calc(var(--leaf) + 8px) minmax(min-content,1fr) calc(var(--leaf) + 8px);grid-template-rows:calc(var(--leaf) + 8px) minmax(min-content,1fr) calc(var(--leaf) + 8px);gap:0}
  .enclosure > .parts > .cell[data-index="0"]{grid-area:1/1/4/4}
  .enclosure > .parts > .cell[data-index="1"]{z-index:1;margin:4px}
  [data-op="⿴"] > .parts > [data-index="1"]{grid-area:2/2/3/3}
  [data-op="⿵"] > .parts > [data-index="1"]{grid-area:2/2/4/3}
  [data-op="⿶"] > .parts > [data-index="1"]{grid-area:1/2/3/3}
  [data-op="⿷"] > .parts > [data-index="1"]{grid-area:2/2/3/4}
  [data-op="⿸"] > .parts > [data-index="1"]{grid-area:2/2/4/4}
  [data-op="⿹"] > .parts > [data-index="1"]{grid-area:2/1/4/3}
  [data-op="⿺"] > .parts > [data-index="1"]{grid-area:1/2/3/4}
  [data-op="⿼"] > .parts > [data-index="1"]{grid-area:2/1/3/3}
  [data-op="⿽"] > .parts > [data-index="1"]{grid-area:1/1/3/3}
  .enclosure > .parts > [data-index="0"] > .part{place-items:start}
  [data-op="⿵"] > .parts > [data-index="0"] > .part{place-items:start center}
  [data-op="⿶"] > .parts > [data-index="0"] > .part{place-items:end center}
  [data-op="⿷"] > .parts > [data-index="0"] > .part{place-items:center start}
  [data-op="⿼"] > .parts > [data-index="0"] > .part{place-items:center end}
  [data-op="⿹"] > .parts > [data-index="0"] > .part{place-items:start end}
  [data-op="⿺"] > .parts > [data-index="0"] > .part{place-items:end start}
  [data-op="⿽"] > .parts > [data-index="0"] > .part{place-items:end}
</style>
