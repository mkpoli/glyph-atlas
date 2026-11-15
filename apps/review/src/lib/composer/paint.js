// Descriptions the site holds no drawing of (`loadDrawing` in `ids.js` answers null), drawn in the
// browser by the composer's port, so a form derived for a character page or typed after the last
// drawing run is drawn where it is shown. Each is drawn once a page, as an SVG in the 1000-unit box the
// stored drawings use, and kept as an object URL.
import { composerPainter } from './config.js'

const painted = new Map(), painting = new Map()

/** The browser's drawing of the description `text`: its URL, null where it cannot be drawn, undefined until known. */
export const paintedDrawing = text => painted.get(text.normalize('NFC'))

/** Draws the description `text` once, answering its URL or null. */
export function paintDrawing(text) {
  text = text.normalize('NFC')
  if (painted.has(text)) return Promise.resolve(painted.get(text))
  if (typeof Worker === 'undefined') return Promise.resolve(null)
  if (!painting.has(text)) painting.set(text, composerPainter().paint(text).then(drawn => {
    const url = drawn?.d
      ? URL.createObjectURL(new Blob([`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000"><path d="${drawn.d}"/></svg>`], { type: 'image/svg+xml' }))
      : null
    painted.set(text, url)
    painting.delete(text)
    return url
  }))
  return painting.get(text)
}
