// Which composer export the editor's preview draws from: han-compose's `export-web` output,
// paired with the package's bundled outline files emitted by Vite (`composer-outlines`).
import outlines from 'composer-outlines'
import { COMPOSER_VERSION, composerBase } from './base.js'
import { createDrawer, createPainter } from './client.js'

export { COMPOSER_VERSION, composerBase }

const makeWorker = () => new Worker(new URL('./worker.js', import.meta.url), { type: 'module' })
const origin = () => typeof location === 'undefined' ? 'https://glyphatlas.org/' : location.href

let loaders = null
const composerLoaders = () => loaders ??= Object.fromEntries(
  Object.entries(outlines).map(([name, url]) => [name, new URL(url, origin()).href]),
)

let drawer = null, painter = null
/** The page's one drawer, drawing in a worker from the current export. */
export const composerDrawer = () => drawer ??= createDrawer(makeWorker, composerBase(), { loaders: composerLoaders() })
/** The page's one painter, drawing the sequences the site holds no drawing of (`paint.js`). */
export const composerPainter = () => painter ??= createPainter(makeWorker, composerBase(), { loaders: composerLoaders() })
