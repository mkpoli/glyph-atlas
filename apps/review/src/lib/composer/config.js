// Which composer export the editor's preview draws from: han-compose's `export-web` output, served by
// `ids-input-composer.mkpoli.workers.dev` under `/<version>/`; `composerVersions.ts` names the
// versions this site accepts, and the first is drawn from. VITE_COMPOSER_BASE points a local build
// at another copy laid out the same way (`<base><version>/manifest.json`), such as `/atlas/composer/`.
import { COMPOSER_VERSION } from '../../../../cloudflare/src/composerVersions.ts'
import { createDrawer, createPainter } from './client.js'

export { COMPOSER_VERSION }
const BASE = import.meta.env?.VITE_COMPOSER_BASE || 'https://ids-input-composer.mkpoli.workers.dev/'

const makeWorker = () => new Worker(new URL('./worker.js', import.meta.url), { type: 'module' })
export const composerBase = () => new URL(`${BASE}${COMPOSER_VERSION}/`, typeof location === 'undefined' ? 'https://glyphatlas.org/' : location.href).href

let drawer = null, painter = null
/** The page's one drawer, drawing in a worker from the current export. */
export const composerDrawer = () => drawer ??= createDrawer(makeWorker, composerBase())
/** The page's one painter, drawing the sequences the site holds no drawing of (`paint.js`). */
export const composerPainter = () => painter ??= createPainter(makeWorker, composerBase())
