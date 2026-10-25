// The composer in a Web Worker, so drawing a sequence never holds up the page: it receives
// `{ id, sequence, base }`, draws the sequence from the export at `base`, and answers `{ id, drawn }`
// or `{ id, error }`. The page ends a worker whose answer it no longer needs (`client.js`).
import { compose, fromUrl } from './index.js'

let source = null, at = null
self.onmessage = async ({ data: { id, sequence, base } }) => {
  if (at !== base) { source = fromUrl(base); at = base }
  try { self.postMessage({ id, drawn: await compose(source, sequence) }) }
  catch (error) { self.postMessage({ id, error: String(error?.message ?? error) }) }
}
