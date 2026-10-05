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
  // What came of each image is kept by its address, so a record given a new image (a crop cut again)
  // starts over: painted in its own shape, faded in once it is here.
  let came = $state({ src: null, shape: null }), lost = $state(null)
  const loaded = $derived(came.src === src), failed = $derived(lost === src)
  // Its own size once it is here, so the tone's shape is exact even for a record that does not name it.
  const shape = $derived(loaded ? came.shape : null)
  const arrived = image => {
    if (came.src === image.getAttribute('src')) return
    came = { src: image.getAttribute('src'), shape: image.naturalWidth && image.naturalHeight ? [image.naturalWidth, image.naturalHeight] : null }
    onload(item.id)
  }
  const broke = image => { lost = image.getAttribute('src'); onerror(item.id) }
  // A server-rendered image may have loaded, or failed, before the page hydrated.
  const settled = image => untrack(() => { if (image.complete) { if (image.naturalWidth) arrived(image); else broke(image) } })
</script>
{#if failed}<span class="missing-glyph {kind}" aria-label={t('character.image.unavailable')}>—</span>
{:else}<span class="crop-paint {kind}" class:loaded style={[cropPaint(item, shape), frame].filter(Boolean).join(';')}>{#key src}<img {src} {alt} loading={eager ? 'eager' : 'lazy'} fetchpriority={eager ? 'high' : 'auto'}
  decoding="async" {...rest} onload={event => arrived(event.currentTarget)} onerror={event => broke(event.currentTarget)} {@attach settled} />{/key}</span>{/if}
