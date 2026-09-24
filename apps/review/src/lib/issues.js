export const issues = [
  { id: 'reading', title: 'Wrong character', example: 'ア → カ', hint: 'One character, a different reading', key: '1' },
  { id: 'merged', title: 'Joined characters', example: 'アカ', hint: 'Two or more in one crop', key: '2' },
  { id: 'crop', title: 'Bad crop', example: 'ア', hint: 'Missing strokes, extra ink, or misplaced edges', key: '3' },
  { id: 'blank', title: 'Not a character', example: '', hint: 'Blank paper, a mark or noise', key: '4' },
  { id: 'unclear', title: 'Can’t tell', example: '?', hint: 'Too faint or hard to read', key: '5' },
]
export const issueTitle = id => issues.find(i => i.id === id)?.title || 'Selected'
export const decision = issue => issue === 'unclear' ? { skip: true } : { verdict: 'wrong', issue, correction: null }
export const isSingle = text => [...new Intl.Segmenter('ja', { granularity: 'grapheme' }).segment(text)].length === 1

/**
 * Whether choosing this issue is a decision or a skip.
 *
 * "Can't tell" is an honest answer and not a review: the reader is saying this crop cannot be judged,
 * which is what Skip says. Recording it as a dispute would put words in their mouth and count a crop
 * they declined to judge as work done, so it advances the queue and writes nothing — in the reviewer
 * and in a round alike.
 */
export const isSkip = issue => issue === 'unclear'

/** What the controls call the action that is not a decision. */
export const SKIP_LABEL = 'Skip'

/** Whether the reader asked for no motion; scrolling and focus jumps follow their choice. */
export const prefersReducedMotion = () =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

/** Whether choosing this issue offers suggestions, so the next action has somewhere to go.
 *
 * A character correction offers them as well: the suggestion names the character that was printed,
 * which is the identity layer, and the reader still has to see and choose it. */
export const suggestsReading = issue => ['reading', 'merged', 'character'].includes(issue)

/**
 * Bring the suggestion area into sight, and in the reviewer put the keyboard in it.
 *
 * `focus` is false where several characters are being marked at once: the round shows a suggestion
 * area under every tile it applies to, and pulling focus into one of them would decide for the
 * reader which character they are working on. The area is a target even while the candidates are
 * still being read, so the next action is available before the model answers, and nothing moves
 * later when it does.
 */
export function greetSuggestions(node, { focus = false } = {}) {
  if (!node) return false
  if (!focus) {
    node.scrollIntoView({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
    return false
  }
  // A sticky footer covers the bottom of the scroll box without being part of its geometry, so the
  // browser's own reveal of a focused element puts the candidate underneath the save bar on a narrow
  // screen: the focus lands correctly and the reader cannot see it. The bar and the header are
  // measured and kept clear with scroll margins, which is what the browser uses when it brings the
  // focused element into view, so the heading and the candidate it belongs to stay visible between
  // them. The motion follows the reader's setting, as everywhere else.
  //
  // The bar is a sibling of `.inspector` under the dialog, and the dialog is the element that
  // scrolls, so both are looked up from the dialog rather than from the suggestion block's parent.
  const dialog = node.closest('.character-dialog, dialog')
  const footer = dialog?.querySelector('.inspector-savebar')
  const header = dialog?.querySelector('.inspector-header')
  const clearance = 12
  const bottom = footer ? Math.round(footer.getBoundingClientRect().height) + clearance : clearance
  const top = header ? Math.round(header.getBoundingClientRect().height) + clearance : clearance
  node.style.scrollMarginTop = `${top}px`
  node.style.scrollMarginBottom = `${bottom}px`
  const target = node.querySelector('.suggestion-options button') || node
  target.focus({ preventScroll: true })
  const box = target.getBoundingClientRect()
  if (box.bottom > window.innerHeight - bottom || box.top < top) {
    // The block carries the margins, so revealing the candidate reveals the heading above it.
    node.scrollIntoView({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
  }
  return true
}
