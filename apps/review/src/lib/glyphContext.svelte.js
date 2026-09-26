// The glyph whose place on its page is on show: the one under the pointer or keyboard focus, else the
// last one clicked. `side` keeps the panel on the half of the window away from the glyph that placed
// it: left or right, or top or bottom on a narrow screen. A pinned panel stays where it was put.
export const glyphContext = $state({ hovered: null, pinned: null, side: 'right' })

let showing = 0, hiding = 0
function sideOf(node) {
  const box = node.getBoundingClientRect()
  return innerWidth <= 700 ? (box.top + box.height / 2 > innerHeight / 2 ? 'top' : 'bottom')
    : box.left + box.width / 2 > innerWidth / 2 ? 'left' : 'right'
}

/** Shows a glyph's context while the pointer rests on `node`, and with `pin` keeps it after a click. */
export function showsContext(node, { id, pin = true }) {
  function show() {
    clearTimeout(showing); clearTimeout(hiding)
    const reveal = () => {
      if (!node.isConnected) return
      glyphContext.hovered = id
      if (!glyphContext.pinned) glyphContext.side = sideOf(node)
    }
    // Once a context is on show, moving to the next glyph replaces it at once.
    if (glyphContext.hovered || glyphContext.pinned) reveal()
    else showing = setTimeout(reveal, 200)
  }
  function hide() {
    clearTimeout(showing)
    hiding = setTimeout(() => { if (glyphContext.hovered === id) glyphContext.hovered = null }, 120)
  }
  const enter = event => { if (event.pointerType === 'mouse') show() }
  const focus = () => { if (node.matches(':focus-visible')) show() }
  const click = () => { if (!pin) return; glyphContext.pinned = id; glyphContext.side = sideOf(node) }
  node.addEventListener('pointerenter', enter)
  node.addEventListener('pointerleave', hide)
  node.addEventListener('focus', focus)
  node.addEventListener('blur', hide)
  node.addEventListener('click', click)
  return {
    update(next) { ({ id, pin = true } = next) },
    destroy() {
      node.removeEventListener('pointerenter', enter)
      node.removeEventListener('pointerleave', hide)
      node.removeEventListener('focus', focus)
      node.removeEventListener('blur', hide)
      node.removeEventListener('click', click)
      if (glyphContext.hovered === id) glyphContext.hovered = null
    },
  }
}

export function clearContext() { clearTimeout(showing); clearTimeout(hiding); glyphContext.hovered = null; glyphContext.pinned = null }
