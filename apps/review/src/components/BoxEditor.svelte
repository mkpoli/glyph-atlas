<script module>
  export const EDGES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']

  /** `from` moved by (dx, dy), or resized from `edge` by them, in the box's own units; never under two units. */
  export function dragged(from, edge, dx, dy) {
    let { x, y, w, h } = from
    if (!edge) return { x: x + dx, y: y + dy, w, h }
    if (edge.includes('w')) { x = Math.min(from.x + dx, from.x + from.w - 2); w = from.x + from.w - x }
    if (edge.includes('e')) w = Math.max(2, from.w + dx)
    if (edge.includes('n')) { y = Math.min(from.y + dy, from.y + from.h - 2); h = from.y + from.h - y }
    if (edge.includes('s')) h = Math.max(2, from.h + dy)
    return { x, y, w, h }
  }

  const DIRECTIONS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }

  /**
   * The box an arrow key makes: moved by `step` box units each way, or with Shift grown or shrunk
   * from its right and bottom. Null for any other key.
   */
  export function nudged(box, event, step) {
    const direction = DIRECTIONS[event.key]
    if (!direction) return null
    const [x, y] = direction.map((d, i) => d * step[i])
    return event.shiftKey ? { ...box, w: Math.max(2, box.w + x), h: Math.max(2, box.h + y) } : { ...box, x: box.x + x, y: box.y + y }
  }
</script>

<script>
  // A box over an image that a drag inside moves and its eight handles resize. `box` is in the image's
  // own units; `scale` is screen pixels per unit and `origin` the screen position of the image's
  // corner, inside the positioned element that holds the editor. `onedit` hears each new box, and
  // whether it was a move or a resize; the caller keeps it inside its bounds.
  let { box, scale, origin, onedit, disabled = false } = $props()
  let pointer = null

  const style = $derived(box ? `left:${origin.x + box.x * scale}px;top:${origin.y + box.y * scale}px;width:${box.w * scale}px;height:${box.h * scale}px` : '')

  function down(event) {
    if (disabled || event.button !== 0 || pointer) return
    // The drag is the editor's: the view under it must not pan or draw.
    event.preventDefault()
    event.stopPropagation()
    pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, edge: event.target.dataset?.edge ?? null, from: { ...box } }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  function move(event) {
    if (!pointer || pointer.id !== event.pointerId) return
    onedit(dragged(pointer.from, pointer.edge, (event.clientX - pointer.x) / scale, (event.clientY - pointer.y) / scale), pointer.edge ? 'resize' : 'move')
  }
  function up(event) {
    if (!pointer || pointer.id !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    pointer = null
  }
</script>

<span class="box-editor" class:disabled {style} aria-hidden="true" onpointerdown={down} onpointermove={move} onpointerup={up}
      onpointercancel={up} onlostpointercapture={() => pointer = null}>{#each EDGES as edge (edge)}<span class="handle {edge}" data-edge={edge}></span>{/each}</span>

<style>
  /* A thin outline with small square handles, each with a finger-sized hit area. */
  .box-editor{position:absolute;pointer-events:auto;cursor:move;outline:1px solid var(--accent-solid);outline-offset:0;touch-action:none;box-shadow:0 0 12px 3px light-dark(rgb(24 20 17 / 24%), rgb(24 20 17 / 24%))}
  .box-editor.disabled{pointer-events:none}
  .handle{position:absolute;width:8px;height:8px;margin:-4px 0 0 -4px;background:#fff;border:1.5px solid #2b2930;border-radius:1px;pointer-events:auto;touch-action:none}
  .handle::before{content:"";position:absolute;inset:-9px}
  .handle.nw{left:0;top:0;cursor:nwse-resize}.handle.n{left:50%;top:0;cursor:ns-resize}.handle.ne{left:100%;top:0;cursor:nesw-resize}
  .handle.e{left:100%;top:50%;cursor:ew-resize}.handle.se{left:100%;top:100%;cursor:nwse-resize}.handle.s{left:50%;top:100%;cursor:ns-resize}
  .handle.sw{left:0;top:100%;cursor:nesw-resize}.handle.w{left:0;top:50%;cursor:ew-resize}
</style>
