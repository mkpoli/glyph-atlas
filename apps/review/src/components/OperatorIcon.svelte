<script>
  // An operator's icon in a 24-unit square, matching IDS Workshop (ids-input): each part is a
  // tinted block (`part`), an enclosing part is a thick stroke round its closed sides (`wrap`),
  // ⿾ and ⿿ show an F mirrored across a dashed axis or rotated half round about the centre, 〾 is
  // a part under a wave, and ㇯ is a part with a cut taken out of it. Strokes keep their width at
  // any size.
  let { op, size = 22 } = $props()

  const ICON = {
    '⿰': { part: [[3, 3, 10.75, 21], [13.25, 3, 21, 21]] },
    '⿱': { part: [[3, 3, 21, 10.75], [3, 13.25, 21, 21]] },
    '⿲': { part: [[2.5, 3, 8, 21], [9.25, 3, 14.75, 21], [16, 3, 21.5, 21]] },
    '⿳': { part: [[3, 2.5, 21, 8], [3, 9.25, 21, 14.75], [3, 16, 21, 21.5]] },
    '⿴': { wrap: 'M4 4H20V20H4Z', part: [[8, 8, 16, 16]] },
    '⿵': { wrap: 'M4 21V4H20V21', part: [[8, 8, 16, 21]] },
    '⿶': { wrap: 'M4 3V20H20V3', part: [[8, 3, 16, 16]] },
    '⿷': { wrap: 'M21 4H4V20H21', part: [[8, 8, 21, 16]] },
    '⿼': { wrap: 'M3 4H20V20H3', part: [[3, 8, 16, 16]] },
    '⿸': { wrap: 'M21 4H4V21', part: [[8, 8, 21, 21]] },
    '⿹': { wrap: 'M3 4H20V21', part: [[3, 8, 16, 21]] },
    '⿺': { wrap: 'M4 3V20H21', part: [[8, 3, 21, 16]] },
    '⿽': { wrap: 'M20 3V20H3', part: [[3, 3, 16, 16]] },
    '⿻': { part: [[3, 3, 15, 15], [9, 9, 21, 21]] },
    // An F and its mirror image across the axis.
    '⿾': { mark: ['M9.5 5H4V19M4 11.5H8.5'], accent: ['M14.5 5H20V19M20 11.5H15.5'], dashed: ['M12 2.5V21.5'] },
    // An F, the same F turned half round about the centre, and the turn.
    '⿿': { mark: ['M8.5 3H3V12M3 7H7'], accent: ['M15.5 21H21V12M21 17H17', 'M11.5 3.2A8.8 8.8 0 0 1 20.6 10.5', 'M20.6 10.5l-2.5-2.3M20.6 10.5l1.6-3'], dot: [12, 12] },
    // A part under the wave of "a variant of".
    '〾': { part: [[5, 10, 19, 21]], accent: ['M3.5 5.5q2.125-2.5 4.25 0t4.25 0 4.25 0 4.25 0'] },
    // A part with a second taken out of it.
    '㇯': { part: [[3, 3, 21, 21]], hole: [8.5, 8.5, 18, 18], accent: ['M10.75 13.25H15.75'] },
  }
  const icon = $derived(ICON[op] ?? {})
</script>

<svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" class="ids-icon" data-op={op}>
  {#each icon.part ?? [] as [x0, y0, x1, y1], i (i)}
    <rect class="part" x={x0} y={y0} width={x1 - x0} height={y1 - y0} rx="1.5" />
  {/each}
  {#if icon.hole}
    {@const [x0, y0, x1, y1] = icon.hole}
    <rect class="hole" x={x0} y={y0} width={x1 - x0} height={y1 - y0} rx="1" />
  {/if}
  {#if icon.wrap}<path class="wrap" d={icon.wrap} />{/if}
  {#each icon.dashed ?? [] as d (d)}<path class="axis" d={d} />{/each}
  {#each icon.mark ?? [] as d (d)}<path class="mark" d={d} />{/each}
  {#each icon.accent ?? [] as d (d)}<path class="accent" d={d} />{/each}
  {#if icon.dot}<circle class="dot" cx={icon.dot[0]} cy={icon.dot[1]} r="1.3" />{/if}
</svg>

<style>
  .ids-icon{display:block;flex:none;overflow:visible;
    --_icon-ink:var(--ids-icon-ink,var(--ink-soft));--_icon-accent:var(--ids-accent,var(--accent))}
  .ids-icon :is(rect,path){vector-effect:non-scaling-stroke;stroke-linejoin:round;stroke-linecap:round}
  .part{fill:color-mix(in srgb,var(--_icon-ink) 22%,transparent);stroke:var(--_icon-ink);stroke-width:1.25}
  [data-op="⿻"] .part:last-of-type{fill:color-mix(in srgb,var(--_icon-accent) 22%,transparent);stroke:var(--_icon-accent)}
  .wrap{fill:none;stroke:var(--_icon-ink);stroke-width:2.6}
  .mark{fill:none;stroke:var(--_icon-ink);stroke-width:2.2}
  .accent{fill:none;stroke:var(--_icon-accent);stroke-width:2.2}
  .axis{fill:none;stroke:var(--_icon-accent);stroke-width:1.1;stroke-dasharray:2 1.6}
  .dot{fill:var(--_icon-accent)}
  .hole{fill:var(--ids-surface,var(--surface));stroke:var(--_icon-accent);stroke-width:1.4}
</style>
