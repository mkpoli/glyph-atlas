<script>
  import { onMount } from 'svelte'
  import { catalogue, randomSeed } from '../lib/client.js'
  import Glyph from './Glyph.svelte'
  // Crops from the collection, a few at a time swapping for others, as a wall behind the sign-in form.
  let { count = 24, columns = 6 } = $props()
  let tiles = $state([]), reserve = [], wall
  const still = () => matchMedia('(prefers-reduced-motion: reduce)').matches
  onMount(() => {
    let timer, closed = false
    catalogue({ limit: count * 2, seed: randomSeed() }).then(result => {
      if (closed) return
      const shown = result.items.filter(item => item.image && (item.origin !== 'corpus' || item.proxyable))
      tiles = shown.slice(0, count).map((item, slot) => ({ slot, item, turn: 0 }))
      reserve = shown.slice(count)
      if (still() || !reserve.length) return
      timer = setInterval(() => {
        const slot = Math.floor(Math.random() * tiles.length), tile = tiles[slot]
        if (!tile) return
        reserve.push(tile.item)
        tiles[slot] = { slot, item: reserve.shift(), turn: tile.turn + 1 }
      }, 1400)
    }).catch(() => {})
    return () => { closed = true; clearInterval(timer) }
  })
  // The light follows the pointer across the wall.
  function follow(event) {
    const box = wall.getBoundingClientRect()
    wall.style.setProperty('--x', `${event.clientX - box.left}px`)
    wall.style.setProperty('--y', `${event.clientY - box.top}px`)
  }
</script>

<div class="specimen-wall" bind:this={wall} onpointermove={follow} aria-hidden="true" style:--columns={columns}>
  {#each tiles as tile (tile.slot)}
    <div class="specimen" style:--delay={`${(tile.slot % columns) * 60 + Math.floor(tile.slot / columns) * 90}ms`}>
      {#key tile.turn}<Glyph item={tile.item} alt="" class="specimen-crop" />{/key}
    </div>
  {/each}
</div>

<style>
  .specimen-wall { position: relative; display: grid; grid-template-columns: repeat(var(--columns), minmax(0, 1fr)); gap: 1px;
    background: var(--line); border: 1px solid var(--line); --x: 50%; --y: 40%;
    mask-image: radial-gradient(ellipse 85% 75% at 50% 45%, #000 45%, transparent 100%); }
  .specimen-wall::after { content: ''; position: absolute; inset: 0; pointer-events: none;
    background: radial-gradient(260px circle at var(--x) var(--y), var(--accent-wash), transparent 70%); }
  .specimen { aspect-ratio: 1; display: grid; place-items: center; padding: 18%; background: var(--surface-tile);
    animation: rise .7s cubic-bezier(.2, .7, .2, 1) both; animation-delay: var(--delay); overflow: hidden; }
  .specimen :global(.specimen-crop) { grid-area: 1 / 1; width: 100%; height: 100%; animation: ink 1.1s ease both; }
  @keyframes rise { from { opacity: 0; transform: translateY(10px) } }
  @keyframes ink { from { opacity: 0; filter: blur(6px); transform: scale(.92) } }
  @media (prefers-reduced-motion: reduce) { .specimen, .specimen :global(.specimen-crop) { animation: none } }
</style>
