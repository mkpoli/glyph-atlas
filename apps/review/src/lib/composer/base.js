// The export URL the composer reads from (`ids-input-composer.mkpoli.workers.dev` by default, or
// `VITE_COMPOSER_BASE` for a local copy such as `/atlas/composer/`). Kept separate from the Vite
// virtual asset map so unit tests can import `composerBase` outside Vite.
import { COMPOSER_VERSION } from '../../../../cloudflare/src/composerVersions.ts'

export { COMPOSER_VERSION }
const BASE = import.meta.env?.VITE_COMPOSER_BASE || 'https://ids-input-composer.mkpoli.workers.dev/'
const origin = () => typeof location === 'undefined' ? 'https://glyphatlas.org/' : location.href
export const composerBase = () => new URL(`${BASE}${COMPOSER_VERSION}/`, origin()).href
