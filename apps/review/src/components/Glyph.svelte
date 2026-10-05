<script>
  // A crop image. Its box shows the crop's paper colour in the crop's shape from the first paint
  // (`cropPaint`), and the image fades in over it once its pixels arrive. Layout classes go on the box
  // (`class`), which takes the place and size an image would, with any style of its own (`frame`); the
  // image fills it, contained.
  import { untrack } from 'svelte'
  import { t } from '../lib/i18n.svelte.js'
  import { cropPaint } from '../lib/cropPaint.js'
  let { item, src = item.image, alt = t('character.glyph.alt', { label: item.label }), class: kind = 'glyph-image', frame = '', eager = false,
    onload = () => {}, onerror = () => {}, ...rest } = $props()
  let failed = $state(false), loaded = $state(false), shape = $state(null)
  // Its own size once it is here, so the tone's shape is exact even for a record that does not name it.
  const arrived = image => {
    if (loaded) return
    loaded = true
    if (image.naturalWidth && image.naturalHeight) shape = [image.naturalWidth, image.naturalHeight]
    onload(item.id)
  }
  const broke = () => { failed = true; onerror(item.id) }
  // A server-rendered image may have loaded, or failed, before the page hydrated.
  const settled = image => untrack(() => { if (image.complete) { if (image.naturalWidth) arrived(image); else broke() } })
</script>
{#if failed}<span class="missing-glyph {kind}" aria-label={t('character.image.unavailable')}>—</span>
{:else}<span class="crop-paint {kind}" class:loaded style={[cropPaint(item, shape), frame].filter(Boolean).join(';')}><img {src} {alt} loading={eager ? 'eager' : 'lazy'} fetchpriority={eager ? 'high' : 'auto'}
  decoding="async" {...rest} onload={event => arrived(event.currentTarget)} onerror={broke} {@attach settled} /></span>{/if}
