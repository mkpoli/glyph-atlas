<script>
  // One occurrence of a pair or trigram as it sits on the page: a crop's context render clipped to the
  // run (`page`, worked out by the Worker's `ngramPage`). Where no render holds the whole run, or the
  // render cannot be loaded, each crop is drawn at its own place on the page, all at one scale. Every
  // character opens its crop in the inspector.
  import { t } from '../lib/i18n.svelte.js'

  let { crops, page = null, oninspect } = $props()
  let broken = $state(false)
  const whole = $derived(page && !broken)
  // A crop's place on the page; a crop without one takes the next cell of a strip.
  const cells = $derived(crops.every(crop => crop.crop_box) ? crops.map(crop => crop.crop_box) : crops.map((_, i) => ({ x: i, y: 0, w: 1, h: 1 })))
  const view = $derived.by(() => {
    if (whole) return page.region
    const left = Math.min(...cells.map(c => c.x)), top = Math.min(...cells.map(c => c.y))
    const margin = Math.max(...cells.flatMap(c => [c.w, c.h])) / 5
    return { x: left - margin, y: top - margin, w: Math.max(...cells.map(c => c.x + c.w)) - left + 2 * margin, h: Math.max(...cells.map(c => c.y + c.h)) - top + 2 * margin }
  })
  // A crop's image is cut with a margin of 8% of its longer side around its box (`atlas.crop_bounds`).
  const cut = box => { const pad = Math.max(box.w, box.h) * 0.08; return { x: box.x - pad, y: box.y - pad, width: box.w + 2 * pad, height: box.h + 2 * pad } }
  function pressed(event, crop) {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    oninspect(crop.id)
  }
</script>

<svg class="ngram-image" viewBox="{view.x} {view.y} {view.w} {view.h}" preserveAspectRatio="xMidYMid meet" role="group" aria-label={crops.map(crop => crop.label).join('')}>
  <!-- The element is wider or taller than the run; the inner viewport keeps the page outside it unseen. -->
  <svg x={view.x} y={view.y} width={view.w} height={view.h} viewBox="{view.x} {view.y} {view.w} {view.h}">
    {#if whole}<image href={page.image} x={page.box.x} y={page.box.y} width={page.box.w} height={page.box.h} preserveAspectRatio="none" onerror={() => { broken = true }} />
    {:else}{#each crops as crop, i (i)}<image href={crop.image} {...cut(cells[i])} />{/each}{/if}
  </svg>
  {#each crops as crop, i (i)}
    <rect class="ngram-hit" x={cells[i].x} y={cells[i].y} width={cells[i].w} height={cells[i].h} role="button" tabindex="0" data-unit={crop.id}
          aria-label={t('explore.tile.inspect', { label: crop.label })} onclick={() => oninspect(crop.id)} onkeydown={event => pressed(event, crop)} />
  {/each}
</svg>

<style>
  .ngram-image{display:block;width:100%;height:100%}
  .ngram-hit{fill:transparent;stroke:transparent;stroke-width:2px;vector-effect:non-scaling-stroke;cursor:pointer;outline:none}
  .ngram-hit:hover,.ngram-hit:focus-visible{stroke:var(--accent)}
</style>
