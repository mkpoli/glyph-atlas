// Which composer export the editor's preview draws from: han-compose's `export-web` output, served by
// the project that owns the drawing port, `ids-input-composer.mkpoli.workers.dev`, under
// `/<version>/`; `composerVersions.ts` names the versions this site accepts from it, and the first is
// drawn from. VITE_COMPOSER_BASE points a local build at another copy laid out the same way
// (`<base><version>/manifest.json`), such as `/atlas/composer/` for the copies this Worker holds.
import { COMPOSER_VERSION } from '../../../../cloudflare/src/composerVersions.ts'
import { createDrawer } from './client.js'

export { COMPOSER_VERSION }
const BASE = import.meta.env?.VITE_COMPOSER_BASE || 'https://ids-input-composer.mkpoli.workers.dev/'

let drawer = null
/** The page's one drawer, drawing in a worker from the current export. */
export const composerDrawer = () => drawer ??= createDrawer(
  () => new Worker(new URL('./worker.js', import.meta.url), { type: 'module' }),
  new URL(`${BASE}${COMPOSER_VERSION}/`, location.href).href)
