<script>
  // What the description being edited looks like, at 1em and at 5em, in three steps: at once, its
  // components set in the page font in the boxes their operators give them; once the composer, in a
  // worker, has drawn it, that drawing; and where the site already holds a drawing of the sequence,
  // that one, labelled as final. A description with an empty slot, or larger than a preview draws,
  // stays rough. A drawing using an operator the composer does not draw is labelled approximate.
  import { composerDrawer } from '../lib/composer/config.js'
  import { tooLarge } from '../lib/composer/index.js'
  import { caption } from '../lib/composer/preview.js'
  import { roughLayout } from '../lib/composer/rough.js'
  import { loadDrawing } from '../lib/ids.js'
  import { t } from '../lib/i18n.svelte.js'
  let { tree, sequence = '', whole = false } = $props()

  const LICENSING = 'https://github.com/mkpoli/glyph-atlas/blob/main/docs/licensing.md#composer-drawings'
  const id = `ids-cut-${Math.random().toString(36).slice(2, 8)}`
  const leaves = $derived(roughLayout(tree ?? null))
  const capped = $derived(whole && tooLarge(tree))
  let composed = $state(null), failed = $state(false), final = $state(null)

  // A whole description is drawn after a short pause in typing. A newer text ends the drawing of the
  // one before, and an answer for a text since replaced is dropped.
  $effect(() => {
    const text = whole && !capped ? sequence : ''
    composed = null
    failed = false
    final = null
    if (!text) return
    let live = true
    const timer = setTimeout(() => {
      composerDrawer().draw(text).then(answer => {
        if (!live) return
        if (answer.status === 'composed') composed = answer.drawn
        else if (answer.status === 'failed') failed = true
      })
      loadDrawing(text).then(url => { if (live) final = url })
    }, 150)
    return () => { live = false; clearTimeout(timer); composerDrawer().cancel() }
  })
  const step = $derived(final ? 'final' : composed ? 'composed' : capped ? 'capped' : failed ? 'failed' : 'rough')
  const said = $derived(caption(step, composed))
</script>

{#snippet drawing(size, name)}
  {#if final}
    <span class="final" style:width={size} style:height={size} style:--drawing={`url("${final}")`}></span>
  {:else}
    <svg viewBox="0 0 1000 1000" width={size} height={size} aria-hidden="true">
      {#if composed}
        {#if composed.cut}
          <mask id="{id}-{name}"><rect width="1000" height="1000" fill="#fff" /><path d={composed.cut} fill="#000" /></mask>
        {/if}
        <path d={composed.d} mask={composed.cut ? `url(#${id}-${name})` : undefined} />
      {:else}
        {#each leaves as leaf, i (i)}
          {@const [x0, y0, x1, y1] = leaf.box.map(v => v * 1000)}
          {#if leaf.char === null}
            <rect class="slot" x={x0 + 30} y={y0 + 30} width={Math.max(x1 - x0 - 60, 1)} height={Math.max(y1 - y0 - 60, 1)} rx="40" />
          {:else}
            <text class:cut={leaf.cut} transform="translate({(x0 + x1) / 2} {(y0 + y1) / 2}) scale({(x1 - x0) * (leaf.flip[0] ? -1 : 1)} {(y1 - y0) * (leaf.flip[1] ? -1 : 1)})"
              font-size="1" text-anchor="middle" dominant-baseline="central">{leaf.char}</text>
          {/if}
        {/each}
      {/if}
    </svg>
  {/if}
{/snippet}

<figure class="ids-preview" data-step={step} data-label={said.label} aria-label={t('form.ids.preview.drawing', { sequence })}>
  <figcaption><span class="ids-preview-small">{@render drawing('1em', 'small')}</span><span class="ids-preview-label">{t(`form.ids.preview.${said.label}`, said.params)}</span></figcaption>
  <div class="ids-preview-large">{@render drawing('100%', 'large')}</div>
  {#if said.credits}
    <p class="ids-preview-credits">{t(`form.ids.preview.${said.credits}`)} <a href={LICENSING} rel="noopener" target="_blank">{t('form.ids.preview.licences')}</a></p>
  {/if}
</figure>

<style>
  .ids-preview{margin:0;display:flex;flex-direction:column;align-items:center;gap:6px;width:7.5em;font-size:16px}
  figcaption{display:flex;align-items:center;justify-content:center;gap:6px;font-size:12px;line-height:1.35;color:var(--muted)}
  .ids-preview-label{text-align:center}
  .ids-preview[data-step="final"] figcaption{color:var(--ink)}
  .ids-preview[data-label="failed"] .ids-preview-label{color:var(--fault)}
  .ids-preview-small{display:inline-grid;flex:none;font-size:20px;color:var(--ink)}
  .ids-preview-large{width:5em;height:5em;font-size:24px;padding:4px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink)}
  .ids-preview-large :is(svg,.final){display:block;width:100%;height:100%}
  .final{display:block;background:currentColor;-webkit-mask:var(--drawing) center/contain no-repeat;mask:var(--drawing) center/contain no-repeat}
  @media (forced-colors: active){.final{forced-color-adjust:none;background:CanvasText}}
  svg path{fill:currentColor}
  svg text{fill:var(--ink-soft);font-family:"GenZui Sans","Noto Serif Tangut","Klee One","LXGW WenKai TC",serif}
  svg text.cut{fill:var(--wrong);opacity:.5}
  svg .slot{fill:none;stroke:var(--line-strong);stroke-width:14;stroke-dasharray:40 30}
  .ids-preview:is([data-step="rough"],[data-step="capped"],[data-step="failed"]) svg text{opacity:.75}
  .ids-preview-credits{margin:0;font-size:11px;line-height:1.45;color:var(--muted);text-align:center}
</style>
