// The drawer for descriptions shown inline in text. It is a composer of its own, so these small
// drawings never cancel the one the IDS editor is making, and one queue serves them all.
import { createComposerDrawer } from './config.js'
import { createInlineDrawings } from './inline-drawings.js'

export { drawingSvgUrl } from './inline-drawings.js'

let drawer = null
/** The composer's drawing of a description, shared by every text that shows it. */
export const inlineDrawing = createInlineDrawings(async text => {
  drawer ??= createComposerDrawer()
  const answer = await drawer.draw(text)
  return answer.status === 'composed' ? answer.drawn : null
})
