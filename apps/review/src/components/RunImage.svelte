<script>
  // One occurrence of a pair or trigram as it sits on the page: a crop's context render clipped to the
  // run (`page`, worked out by the Worker's `runPage`). Where no render holds the whole run, or the
  // render cannot be loaded, each crop is drawn at its own place on the page, all at one scale; a crop
  // without a place takes the next cell along the line, down it or across. Every character opens its
  // crop in the inspector. Until an image arrives its place shows the crop's paper (`cropTone`).
  import { t } from '../lib/i18n.svelte.js'
  import { cropTone } from '../lib/cropPaint.js'

  let { crops, page = null, vertical = true, oninspect } = $props()
  let broken = $state(false)
  const whole = $derived(page && !broken)
  const placed = $derived(crops.every(crop => crop.crop_box))
  const cells = $derived(placed ? crops.map(crop => crop.crop_box) : crops.map((_, i) => ({ x: vertical ? 0 : i, y: vertical ? i : 0, w: 1, h: 1 })))
  const view = $derived.by(() => {
    if (whole) return page.region
    const left = Math.min(...cells.map(c => c.x)), top = Math.min(...cells.map(c => c.y))
    const margin = Math.max(...cells.flatMap(c => [c.w, c.h])) / 5
    return { x: left - margin, y: top - margin, w: Math.max(...cells.map(c => c.x + c.w)) - left + 2 * margin, h: Math.max(...cells.map(c => c.y + c.h)) - top + 2 * margin }
  })
  // A crop's image is cut with a margin of 8% of its longer side around its box (`atlas.crop_bounds`);
  // a crop without a box fills its cell.
  const cut = box => { const pad = placed ? Math.max(box.w, box.h) * 0.08 : 0; return { x: box.x - pad, y: box.y - pad, width: box.w + 2 * pad, height: box.h + 2 * pad } }
  function pressed(event, crop) {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    oninspect(crop.id)
  }
</script>

<svg class="run-image" viewBox="{view.x} {view.y} {view.w} {view.h}" preserveAspectRatio="xMidYMid meet" role="group" aria-label={crops.map(crop => crop.label).join('')}>
  <!-- The element is wider or taller than the run; the inner viewport keeps the page outside it unseen. -->
  <svg x={view.x} y={view.y} width={view.w} height={view.h} viewBox="{view.x} {view.y} {view.w} {view.h}">
    {#if whole}<rect class="run-paper" style={cropTone(crops.find(crop => cropTone(crop)))} x={page.box.x} y={page.box.y} width={page.box.w} height={page.box.h} />
      <image href={page.image} x={page.box.x} y={page.box.y} width={page.box.w} height={page.box.h} preserveAspectRatio="none" onerror={() => { broken = true }} />
    {:else}{#each crops as crop, i (i)}<rect class="run-paper" style={cropTone(crop)} {...cut(cells[i])} /><image href={crop.image} {...cut(cells[i])} />{/each}{/if}
  </svg>
  {#each crops as crop, i (i)}
    <rect class="run-hit" x={cells[i].x} y={cells[i].y} width={cells[i].w} height={cells[i].h} role="button" tabindex="0" data-unit={crop.id}
          aria-label={t('explore.tile.inspect', { label: crop.label })} onclick={() => oninspect(crop.id)} onkeydown={event => pressed(event, crop)} />
  {/each}
</svg>

<style>
  .run-image{display:block;width:100%;height:100%}
  .run-paper{fill:var(--tone,var(--crop-paper))}
  .run-hit{fill:transparent;stroke:transparent;stroke-width:2px;vector-effect:non-scaling-stroke;cursor:pointer;outline:none}
  .run-hit:hover,.run-hit:focus-visible{stroke:var(--accent)}
</style>
