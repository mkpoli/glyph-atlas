// Descriptions the site holds no drawing of (`loadDrawing` in `ids.js` answers null), drawn in the
// browser by han-compose, so a form derived for a character page or typed after the last drawing run
// is drawn where it is shown. Each is drawn once a page, as an SVG in the 1000-unit box the stored
// drawings use, and kept as an object URL. A part no code point names is drawn by the name the
// composer knows it by (`parts.js`).
import { drawingSvg } from 'han-compose/drawing-svg'
import { composerPainter } from './config.js'
import { composerText } from './parts.js'

const painted = new Map(), painting = new Map()
// A sequence that could not be drawn is tried again after five minutes, as `loadDrawing` asks again
// after a miss, so a failed request does not keep it as text for the whole visit.
const MISS_MS = 5 * 60 * 1000

/** The browser's drawing of the description `text`: its URL, null where it cannot be drawn, undefined until known. */
export const paintedDrawing = text => painted.get(text.normalize('NFC'))

/** Draws the description `text` once, answering its URL or null. */
export function paintDrawing(text) {
  text = text.normalize('NFC')
  if (painted.has(text)) return Promise.resolve(painted.get(text))
  if (typeof Worker === 'undefined') return Promise.resolve(null)
  if (!painting.has(text)) painting.set(text, composerText(text).then(sequence => composerPainter().paint(sequence), () => null).then(drawn => {
    const url = drawn?.d ? URL.createObjectURL(new Blob([drawingSvg(drawn)], { type: 'image/svg+xml' })) : null
    painted.set(text, url)
    painting.delete(text)
    if (url === null) setTimeout(() => painted.delete(text), MISS_MS)
    return url
  }))
  return painting.get(text)
}
