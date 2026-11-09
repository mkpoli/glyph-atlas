// Drawings for a description shown inline in text: a form Unicode lacks, a word spelled with a
// sequence. The composer's drawing becomes an SVG data URL for the mask `ScriptText` already paints;
// the queue draws one sequence at a time, so a row of forms neither cancels its own drawings nor waits
// on one that never answers, and a sequence is drawn once a page.

/** How long a sequence the composer could not draw is remembered as having none. */
const MISS_MS = 5 * 60 * 1000
/** How long one drawing may take before the queue passes to the next sequence. */
const TIMEOUT_MS = 30 * 1000

const attribute = value => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;')

/**
 * A composer drawing as an SVG data URL, or null without a path: its ink in the 1000-unit box the
 * composer draws in, masked where ㇯ cuts it.
 * @param {{ d?: string, cut?: string } | null} drawing
 */
export function drawingSvgUrl(drawing) {
  const d = typeof drawing?.d === 'string' ? drawing.d.trim() : ''
  if (!d) return null
  const cut = typeof drawing?.cut === 'string' ? drawing.cut.trim() : ''
  const mask = cut
    ? `<mask id="cut"><rect width="1000" height="1000" fill="#fff"/><path d="${attribute(cut)}" fill="#000"/></mask>`
    : ''
  const ink = `<path d="${attribute(d)}"${cut ? ' mask="url(#cut)"' : ''}/>`
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000">${mask}${ink}</svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

/**
 * Asks the composer for a description's drawing. Sequences are drawn one at a time in the order
 * asked, a sequence is asked once while its answer is kept, and a drawing that fails or takes too
 * long answers null so the queue moves on.
 * @param {(text: string) => Promise<{ d?: string, cut?: string } | null>} draw
 * @param {{ missMs?: number, timeoutMs?: number }} [options]
 */
export function createInlineDrawings(draw, { missMs = MISS_MS, timeoutMs = TIMEOUT_MS } = {}) {
  /** @type {Map<string, Promise<{ d?: string, cut?: string } | null>>} */
  const drawings = new Map()
  let chain = Promise.resolve()

  /** @param {Promise<{ d?: string, cut?: string } | null>} promise */
  const within = promise => new Promise(resolve => {
    const timer = setTimeout(() => resolve(null), timeoutMs)
    promise.then(value => { clearTimeout(timer); resolve(value) }, () => { clearTimeout(timer); resolve(null) })
  })

  return (/** @type {string} */ text) => {
    const key = text.normalize('NFC')
    const known = drawings.get(key)
    if (known) return known
    const task = chain.then(() => within(draw(key))).then(drawing => drawing ?? null, () => null)
    chain = task
    drawings.set(key, task)
    task.then(drawing => {
      if (drawing) return
      setTimeout(() => { if (drawings.get(key) === task) drawings.delete(key) }, missMs)
    })
    return task
  }
}
