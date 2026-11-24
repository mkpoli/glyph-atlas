/** Puts an open popover under its anchor, or over it where more of it fits, kept in the viewport; one
 *  taller than either space scrolls. */
export function placeBeside(popup, anchor) {
  const scrollTop = popup.scrollTop
  popup.style.maxHeight = ''
  const at = anchor.getBoundingClientRect()
  const box = popup.getBoundingClientRect()
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
