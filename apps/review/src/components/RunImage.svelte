<script>
  // One occurrence of a pair or trigram as it sits on the page: a crop's context render clipped to the
  // run (`page`, worked out by the Worker's `runPage`). Where no render holds the whole run, or the
  // render cannot be loaded, each crop is drawn at its own place on the page, all at one scale; a crop
  // without a place takes the next cell along the line, down it or across. Until an image arrives its
  // place shows the crop's paper (`cropTone`). With `oninspect`, every character opens its crop in the
  // inspector. With `context`, the whole render is shown and the run's characters are outlined in it;
  // without it, a render its service can cut is loaded as the run's region alone (`page.cut`), falling back to
  // the whole render if the cut cannot be loaded. With `fill` (the element's height over its width), the
  // view widens or deepens around the run to the element's shape, so the page around it fills the element;
  // a cut is then asked for over that wider part of the render.
  import { t } from '../lib/i18n.svelte.js'
  import { cropShape, cropTone } from '../lib/cropPaint.js'

  let { crops, page = null, vertical = true, oninspect = null, context = false, fill = null } = $props()
  let broken = $state(false), uncut = $state(false)
  const whole = $derived(page && !broken)
  // The run's region as its own image, where the render's service cuts one; the whole render otherwise.
  const cutShown = $derived(whole && !context && page.cut && !uncut)
  // The region, grown about its centre to `fill`'s shape, and the part of the render inside it.
  const shaped = $derived.by(() => {
    if (!whole || context || !fill) return null
    const { x, y, w, h } = page.region, grown = { w: Math.max(w, h / fill), h: Math.max(h, w * fill) }
    const view = { x: x - (grown.w - w) / 2, y: y - (grown.h - h) / 2, ...grown }
    const left = Math.floor(Math.max(view.x, page.box.x)), top = Math.floor(Math.max(view.y, page.box.y))
    const shown = { x: left, y: top, w: Math.ceil(Math.min(view.x + view.w, page.box.x + page.box.w)) - left, h: Math.ceil(Math.min(view.y + view.h, page.box.y + page.box.h)) - top }
    return { view, shown }
  })
  const cutAt = $derived(shaped?.shown ?? page?.region)
  const cutHref = $derived(page?.cut && shaped ? recut(page.cut, shaped.shown) : page?.cut)
  const picture = $derived(cutShown ? { href: cutHref, ...cutAt } : whole ? { href: page.image, ...page.box } : null)
  // A server-rendered image can fail before the page hydrates, and an SVG image keeps no record of it, so
  // the cut is asked for once more (from the browser's cache) to learn whether it came.
  $effect(() => {
    if (!cutShown) return
    const probe = new Image()
    probe.onerror = () => { uncut = true }
    probe.src = cutHref
    return () => { probe.onerror = null }
  })
  const placed = $derived(crops.every(crop => crop.crop_box))
  const cells = $derived(placed ? crops.map(crop => crop.crop_box) : crops.map((_, i) => ({ x: vertical ? 0 : i, y: vertical ? i : 0, w: 1, h: 1 })))
  const view = $derived.by(() => {
    if (whole) return context ? page.box : shaped?.view ?? page.region
    const left = Math.min(...cells.map(c => c.x)), top = Math.min(...cells.map(c => c.y))
    const margin = Math.max(...cells.flatMap(c => [c.w, c.h])) / 5
    return { x: left - margin, y: top - margin, w: Math.max(...cells.map(c => c.x + c.w)) - left + 2 * margin, h: Math.max(...cells.map(c => c.y + c.h)) - top + 2 * margin }
  })
  // A crop's image is cut with a margin of 8% of its longer side around its box (`atlas.crop_bounds`);
  // a crop without a box fills its cell.
  // The cut's URL for another region of the same service, at the width the Worker asks for one
  // (`runCut`: at most 660 × 600, never enlarged).
  function recut(url, r) {
    const width = Math.max(1, Math.round(r.w * Math.min(1, 660 / r.w, 600 / r.h)))
    return url.replace(/\/\d+,\d+,\d+,\d+\/\d+,\//, `/${r.x},${r.y},${r.w},${r.h}/${width},/`)
  }
  const cut = box => { const pad = placed ? Math.max(box.w, box.h) * 0.08 : 0; return { x: box.x - pad, y: box.y - pad, width: box.w + 2 * pad, height: box.h + 2 * pad } }
  // The paper under a crop: its image's own rectangle, which an image with no place is contained and
  // centred in its square cell by, as the image itself is.
  function paper(crop, cell) {
    const area = cut(cell), shape = placed ? null : cropShape(crop)
    if (!shape) return area
    const k = Math.min(area.width / shape[0], area.height / shape[1]), width = shape[0] * k, height = shape[1] * k
    return { x: area.x + (area.width - width) / 2, y: area.y + (area.height - height) / 2, width, height }
  }
  function pressed(event, crop) {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    oninspect(crop.id)
  }
</script>

<svg class="run-image" viewBox="{view.x} {view.y} {view.w} {view.h}" preserveAspectRatio="xMidYMid meet" role="group" aria-label={crops.map(crop => crop.label).join('')}>
  <!-- The element is wider or taller than the run; the inner viewport keeps the page outside it unseen. -->
  <svg x={view.x} y={view.y} width={view.w} height={view.h} viewBox="{view.x} {view.y} {view.w} {view.h}">
    {#if whole}<rect class="run-paper" style={cropTone(crops.find(crop => cropTone(crop)))} x={shaped ? view.x : page.box.x} y={shaped ? view.y : page.box.y} width={shaped ? view.w : page.box.w} height={shaped ? view.h : page.box.h} />
      {#key picture.href}<image href={picture.href} x={picture.x} y={picture.y} width={picture.w} height={picture.h} preserveAspectRatio="none"
        onerror={() => { if (cutShown) uncut = true; else broken = true }} />{/key}
    {:else}{#each crops as crop, i (i)}<rect class="run-paper" style={cropTone(crop)} {...paper(crop, cells[i])} /><image href={crop.image} {...cut(cells[i])} />{/each}{/if}
  </svg>
  {#if oninspect}{#each crops as crop, i (i)}
    <rect class="run-hit" class:marked={context && whole} x={cells[i].x} y={cells[i].y} width={cells[i].w} height={cells[i].h} role="button" tabindex="0" data-unit={crop.id}
          aria-label={t('explore.tile.inspect', { label: crop.label })} onclick={() => oninspect(crop.id)} onkeydown={event => pressed(event, crop)} />
  {/each}{/if}
</svg>

<style>
  .run-image{display:block;width:100%;height:100%}
  .run-paper{fill:var(--tone,var(--crop-paper))}
  .run-hit{fill:transparent;stroke:transparent;stroke-width:2px;vector-effect:non-scaling-stroke;cursor:pointer;outline:none}
  .run-hit:hover,.run-hit:focus-visible{stroke:var(--accent)}
  .run-hit.marked{stroke:var(--accent);stroke-opacity:.55}
  .run-hit.marked:hover,.run-hit.marked:focus-visible{stroke-opacity:1;stroke-width:3px}
</style>
