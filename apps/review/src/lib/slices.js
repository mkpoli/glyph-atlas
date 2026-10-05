// A long list in a scrolling panel is drawn a slice at a time: laying out thousands of buttons costs a
// slow computer tens of seconds, during which the page answers nothing. `nearing` is an attachment for
// an element after the drawn part; it asks for the next slice as that element comes within reach of the
// visible part of the box that scrolls it.

/** The number of entries a list draws at first, and adds each time its end comes near. */
export const SLICE = 240

// The nearest box around an element that scrolls it: one that may scroll and has more than it shows. A
// box styled to scroll that holds everything (the browse panel's grid, whose panel scrolls instead) is
// passed over. The reach is measured from that box's visible part: measured from the window, as it is
// with no root, it would not reach past the box's own edge. Where nothing scrolls yet, the window is the
// root and the next slice comes when the element is seen.
function scroller(element) {
  for (let box = element.parentElement; box; box = box.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(box).overflowY) && box.scrollHeight > box.clientHeight) return box
  }
  return null
}

/** Calls `more` whenever the element comes within `reach` pixels of being seen. */
export function nearing(more, reach = 600) {
  return element => {
    const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) more() },
      { root: scroller(element), rootMargin: `${reach}px` })
    observer.observe(element)
    return () => observer.disconnect()
  }
}
