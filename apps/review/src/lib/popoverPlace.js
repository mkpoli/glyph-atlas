/** Puts an open popover under its anchor, or over it where more of it fits, kept in the viewport; one
 *  taller than either space scrolls. With `side` it goes to the right or the left of its anchor where
 *  there is room, clear of what stands under it. */
export function placeBeside(popup, anchor, { side = false } = {}) {
  const scrollTop = popup.scrollTop
  popup.style.maxHeight = ''
  const at = anchor.getBoundingClientRect()
  const box = popup.getBoundingClientRect()
  const beside = !side ? undefined : [at.right + 6, at.left - 6 - box.width].find(left => left >= 8 && left + box.width <= window.innerWidth - 8)
  if (beside !== undefined) {
    popup.style.maxHeight = `${window.innerHeight - 16}px`
    popup.style.left = `${beside}px`
    popup.style.top = `${Math.max(8, Math.min(at.top, window.innerHeight - 8 - box.height))}px`
    popup.scrollTop = scrollTop
    return
  }
  const left = Math.max(8, Math.min(at.left, window.innerWidth - box.width - 8))
  const below = at.bottom + 6
  const spaceBelow = Math.max(0, window.innerHeight - 8 - below)
  const spaceAbove = Math.max(0, at.top - 6 - 8)
  const placeBelow = box.height <= spaceBelow || (box.height > spaceAbove && spaceBelow >= spaceAbove)
  popup.style.maxHeight = `${placeBelow ? spaceBelow : spaceAbove}px`
  popup.scrollTop = scrollTop
  const height = popup.getBoundingClientRect().height
  popup.style.left = `${left}px`
  popup.style.top = `${placeBelow ? below : at.top - height - 6}px`
}
