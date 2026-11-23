// The variation sequences a part can be drawn as. The export the composer draws from holds none;
// han-compose's newer export of the same fonts does: its `variants.json` lists each character with the
// selectors its font has a glyph of its own for, and it records each sequence's outline by key, in
// shards named by the first four hex digits of the key's SHA-256. Only these are read from it.
import { hexOf } from './sha256.js'

export const VARIANTS = 'https://ids-input-composer.mkpoli.workers.dev/a83fc941166b-sans-compose-layout-v4-f7+variants/'
/** The file of that export holding the record of the sequence `key`. */
export const variantFile = key => { const h = hexOf(key); return `g/${h.slice(0, 2)}/${h.slice(0, 4)}.json` }
