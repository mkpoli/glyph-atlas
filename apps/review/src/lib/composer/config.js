// Which composer export the editor's preview draws from: han-compose's `export-web` output, stored in
// R2 under `composer/<version>/` as the exporter wrote it and served by the Worker under
// /atlas/composer/ for the versions `composerVersions.ts` names; the first is drawn from.
// VITE_COMPOSER_BASE points a local build at another copy laid out the same way
// (`<base><version>/manifest.json`).
import { COMPOSER_VERSION } from '../../../../cloudflare/src/composerVersions.ts'
import { createDrawer } from './client.js'

export { COMPOSER_VERSION }
const BASE = import.meta.env?.VITE_COMPOSER_BASE || '/atlas/composer/'

let drawer = null
/** The page's one drawer, drawing in a worker from the current export. */
export const composerDrawer = () => drawer ??= createDrawer(
  () => new Worker(new URL('./worker.js', import.meta.url), { type: 'module' }),
  new URL(`${BASE}${COMPOSER_VERSION}/`, location.href).href)
