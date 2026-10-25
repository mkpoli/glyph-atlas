<script>
  // The description being edited as the composer draws it, at 5em and at 1em. Each part that is filled
  // is drawn as the final drawing draws it; an empty slot is a placeholder in the box the layout gives
  // it, and the slot being edited takes the editor's selection colour. Until the first drawing is
  // done, the frame shows placeholders where the layout puts each part (even shares before the layout
  // itself has loaded); after that the last drawing stays until the next one is done and fades into
  // it. Where the site holds a drawing of the whole sequence, that drawing is shown, badged as final.
  // A sequence too large to draw, or one the composer cannot draw, shows placeholders and says so.
  // The preview only shows: a sequence with an empty slot is never saved (`IdsComposer`).
  import { fade } from 'svelte/transition'
  import { composerDrawer } from '../lib/composer/config.js'
  import { tooLarge } from '../lib/composer/index.js'
  import { FACE, STROKE, badge, placeholder } from '../lib/composer/preview.js'
  import { roughLayout } from '../lib/composer/rough.js'
  import { OP_NAMES, leaves, loadDrawing, samePath, EMPTY } from '../lib/ids.js'
  import { t } from '../lib/i18n.svelte.js'
  let { tree, sequence = '', chosen = null } = $props()

  const id = `ids-cut-${Math.random().toString(36).slice(2, 8)}`
  const parts = $derived(leaves(tree ?? null))
  const capped = $derived(tooLarge(tree))
  // Even shares of the face, for when the layout has not loaded.
  const even = $derived(roughLayout(tree ?? null).map((leaf, index) => ({ index, empty: leaf.char === null,
    box: [FACE[0] + leaf.box[0] * FACE[2], FACE[1] + leaf.box[1] * FACE[3], (leaf.box[2] - leaf.box[0]) * FACE[2], (leaf.box[3] - leaf.box[1]) * FACE[3]] })))
  let sketched = $state(null), shown = $state(null), step = $state('loading'), shownCount = 0

  // A description is drawn after a short pause in typing. A newer text ends the drawing of the one
  // before, and an answer for a text since replaced is dropped.
  $effect(() => {
    const text = sequence, large = capped
    sketched = null
    if (large) { step = 'capped'; shown = null; composerDrawer().cancel(); return }
    step = 'loading'
    let live = true
    const timer = setTimeout(() => {
      const whole = !text.includes(EMPTY)
      let final = false
      if (whole) loadDrawing(text).then(url => {
        if (!live || !url) return
        final = true
        shown = { key: ++shownCount, url }
        step = 'final'
      })
      composerDrawer().draw(text, sketch => { if (live) sketched = { text, leaves: sketch } }).then(answer => {
        if (!live || final) return
        if (answer.status === 'composed') { shown = { key: ++shownCount, drawn: answer.drawn }; step = 'composed' }
        else if (answer.status === 'failed') { shown = null; step = 'failed' }
      })
    }, 150)
    return () => { live = false; clearTimeout(timer); composerDrawer().cancel() }
  })

  const said = $derived(badge(step, shown?.drawn))
  // The badge says it in a word; its note, in a line under the preview and on hover.
  const NOTES = { approximate: 'form.ids.preview.approximateNote', failed: 'form.ids.preview.failedNote', tooLarge: 'form.ids.preview.tooLargeNote' }
  const noted = $derived(said.label in NOTES)
  // The operators named in words: their characters are too new for most fonts to show.
  const named = $derived(said.params.ops ? { ops: said.params.ops.split(' ').map(op => t(`form.ids.op.${OP_NAMES[op]}`)).join(' · ') } : {})
  const note = $derived(t(NOTES[said.label] ?? `form.ids.preview.${said.label}`, named))
  // The placeholders shown in place of a drawing: where the layout puts each part, else even shares.
  const boxes = $derived(sketched?.text === sequence && !capped ? sketched.leaves : even)
  // Whether the part at a leaf lies in the part being edited.
  const inChosen = index => Boolean(chosen && parts[index] && (samePath(parts[index].path, chosen)
    || parts[index].path.length > chosen.length && chosen.every((x, i) => x === parts[index].path[i])))
  // An empty slot of a drawing by its number, as the leaf it is.
  const slotLeaf = number => parts.map((leaf, index) => ({ leaf, index })).filter(({ leaf }) => leaf.char === null)[number]?.index
</script>

{#snippet slot(box, stroke, selected, faint)}
  {@const p = placeholder(box, stroke)}
  <g class="slot" class:selected class:faint>
    <rect x={p.x} y={p.y} width={p.width} height={p.height} rx={p.rx} class="slot-fill" />
    <rect x={p.x} y={p.y} width={p.width} height={p.height} rx={p.rx} class="slot-line"
      stroke-width={p.dot} pathLength={p.dots} stroke-dasharray="0 1" />
  </g>
{/snippet}

{#snippet picture(name)}
  <div class="frame">
    {#if shown}
      {#key shown.key}
        <div class="layer" transition:fade={{ duration: 180 }}>
          {#if shown.url}
            <span class="final" style:--drawing={`url("${shown.url}")`}></span>
          {:else}
            {@const drawn = shown.drawn}
            <svg viewBox="0 0 1000 1000" aria-hidden="true">
              {#if drawn.cut}
                <mask id="{id}-{name}"><rect width="1000" height="1000" fill="#fff" /><path d={drawn.cut} fill="#000" /></mask>
              {/if}
              <path class="ink" d={drawn.d} mask={drawn.cut ? `url(#${id}-${name})` : undefined} />
              {#each drawn.slots as s (s.index)}
                {@render slot(s.box, drawn.stroke?.[0] ?? STROKE, inChosen(slotLeaf(s.index)), false)}
              {/each}
            </svg>
          {/if}
        </div>
      {/key}
    {:else}
      <div class="layer" transition:fade={{ duration: 180 }}>
        <svg viewBox="0 0 1000 1000" aria-hidden="true">
          {#each boxes as leaf (leaf.index)}
            {@render slot(leaf.box, STROKE, inChosen(leaf.index), !leaf.empty)}
          {/each}
        </svg>
      </div>
    {/if}
  </div>
{/snippet}

<figure class="ids-preview" data-step={step} data-label={said.label} aria-label={t('form.ids.preview.drawing', { sequence })}>
  <div class="ids-preview-large">{@render picture('large')}</div>
  <div class="ids-preview-meta">
    <div class="ids-preview-small">{@render picture('small')}</div>
    <span class="ids-preview-badge" title={note}>{t(`form.ids.preview.${said.label}`)}</span>
  </div>
  {#if noted}<p class="ids-preview-note">{note}</p>{/if}
</figure>

<style>
  .ids-preview{margin:0;display:flex;flex-direction:column;align-items:center;gap:6px;width:120px;flex:none}
  .ids-preview-large{position:relative;width:120px;height:120px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);overflow:hidden}
  .ids-preview-meta{display:flex;align-items:center;justify-content:center;gap:8px;min-height:22px}
  .ids-preview-small{width:20px;height:20px;color:var(--ink)}
  .frame{position:relative;width:100%;height:100%}
  .layer{position:absolute;inset:0}
  .ids-preview-large .layer{inset:6px}
  .layer :is(svg,.final){display:block;width:100%;height:100%}
  .final{background:currentColor;-webkit-mask:var(--drawing) center/100% 100% no-repeat;mask:var(--drawing) center/100% 100% no-repeat}
  @media (forced-colors: active){.final{forced-color-adjust:none;background:CanvasText}.ink{fill:CanvasText}}
  .ink{fill:currentColor}
  .slot-fill{fill:color-mix(in srgb,var(--accent) 6%,transparent)}
  .slot-line{fill:none;stroke:color-mix(in srgb,var(--accent) 50%,var(--muted));stroke-linecap:round}
  .slot.faint .slot-fill{fill:color-mix(in srgb,var(--ink) 3%,transparent)}
  .slot.faint .slot-line{stroke:color-mix(in srgb,var(--ink) 20%,transparent)}
  .slot.selected .slot-fill{fill:color-mix(in srgb,var(--accent) 16%,transparent)}
  .slot.selected .slot-line{stroke:var(--accent)}
  .ids-preview-badge{padding:0 7px;border-radius:999px;font-size:11px;line-height:18px;color:var(--muted);border:1px solid var(--line);white-space:nowrap}
  .ids-preview[data-label="final"] .ids-preview-badge{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--line))}
  .ids-preview[data-label="approximate"] .ids-preview-badge{color:var(--uncertain,var(--muted))}
  .ids-preview:is([data-label="failed"],[data-label="tooLarge"]) .ids-preview-badge{color:var(--fault);border-color:color-mix(in srgb,var(--fault) 35%,var(--line))}
  .ids-preview-note{margin:0;width:100%;font-size:11px;line-height:1.4;color:var(--muted);text-align:center;overflow-wrap:anywhere}
</style>
