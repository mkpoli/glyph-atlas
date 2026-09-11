<script>
  /**
   * One region of a page image, drawn at `scale` css pixels per page pixel.
   *
   * The element is `box.w * scale` by `box.h * scale`; the image inside is positioned so that the
   * region fills it. Overlays (unit boxes, line boxes, a draft rectangle) are passed as children and
   * positioned in page pixels, multiplied by `scale`.
   *
   * With `draw`, a pointer drag inside the element reports the drawn rectangle in page pixels
   * through `oncreate`; `ontrack` always reports the pointer, which is what `s` splits at.
   */
  import { isDrawn, normalizeBox, pagePoint } from '../lib/geometry.js'

  let {
    src = null,
    box = null,
    scale = 1,
    page = null,
    draw = false,
    minimum = 6,
    oncreate = null,
    ontrack = null,
    onfail = null,
    element = $bindable(null),
    children,
    class: klass = '',
    title = '',
  } = $props()

  let natural = $state({ w: 0, h: 0 })
  let draft = $state(null)
  let start = null

  /** The image size: what the file reports once it loads, the page record's size before that. */
  const imageW = $derived(natural.w || page?.width || 0)
  const imageH = $derived(natural.h || page?.height || 0)

  const width = $derived(box ? box.w * scale : 0)
  const height = $derived(box ? box.h * scale : 0)

  function point(event) {
    return pagePoint(event, element, box, scale)
  }

  function down(event) {
    if (!draw || event.button !== 0 || !box) return
    event.preventDefault()
    start = point(event)
    draft = { x: start.x, y: start.y, w: 0, h: 0 }
    element?.setPointerCapture?.(event.pointerId)
  }

  function move(event) {
    if (!box) return
    const point_ = point(event)
    ontrack?.(point_)
    if (start) draft = normalizeBox(start, point_)
  }

  function up(event) {
    if (!start) return
    const drawn = normalizeBox(start, point(event))
    start = null
    draft = null
    if (isDrawn(drawn, minimum)) oncreate?.(drawn)
  }

  function px(value) {
    return `${value}px`
  }
</script>

<div
  class="crop {klass} {draw ? 'picking' : ''}"
  bind:this={element}
  style="width:{px(width)};height:{px(height)}"
  {title}
  role="presentation"
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onpointercancel={() => {
    start = null
    draft = null
  }}
>
  {#if src}
    <img
      {src}
      alt=""
      draggable="false"
      style="width:{px(imageW * scale)};height:{px(imageH * scale)};transform:translate({px(-box.x * scale)},{px(-box.y * scale)})"
      onload={(event) => {
        natural = { w: event.currentTarget.naturalWidth, h: event.currentTarget.naturalHeight }
      }}
      onerror={() => onfail?.()}
    />
  {/if}
  {@render children?.()}
  {#if draft}
    <div
      class="draft"
      style="left:{px((draft.x - box.x) * scale)};top:{px((draft.y - box.y) * scale)};width:{px(draft.w * scale)};height:{px(draft.h * scale)}"
    ></div>
  {/if}
</div>
