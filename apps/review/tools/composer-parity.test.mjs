// The browser composer against han-compose's own drawings. An export (`compose_ids.py export-web OUT
// --parity N --held-out M`) holds sequences the Python composer drew without their characters: in
// `parity.json` a set whose hosts the export was made to hold, and in `parity-held-out.json` one it
// was not, which measures the port as a typed sequence meets it. Each is drawn again here from the
// same export, both drawings rasterised at the size the file names (96 px) and compared by IoU.
// COMPOSER_EXPORT names the export's folder; without it the comparison is skipped.
// COMPOSER_PARITY_LIMIT draws only the first n rows of each set.
import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { compose, createSource } from '../src/lib/composer/index.js'
import { iou, pathMask } from '../src/lib/composer/geometry.js'

// Mean agreement below which the port has drifted from the composer, on the held-out set where the
// export has one.
const FLOOR = 0.95
const folder = process.env.COMPOSER_EXPORT
const files = ['parity-held-out.json', 'parity.json'].filter(name => folder && existsSync(join(folder, name)))
if (!files.length) console.log('composer parity skipped: set COMPOSER_EXPORT to a han-compose export-web folder with parity files')

async function measure(source, parity) {
  const rows = parity.rows.filter(row => row.d).slice(0, Number(process.env.COMPOSER_PARITY_LIMIT) || undefined)
  const scores = []
  for (const row of rows) {
    let score = 0, origins = [], error = null
    try {
      const drawn = await compose(source, row.s, { exclude: row.x, face: row.face ?? null })
      origins = drawn.origins
      score = iou(pathMask(drawn.d, parity.size), pathMask(row.d, parity.size))
    } catch (failure) { error = String(failure.message ?? failure) }
    scores.push({ c: row.c, s: row.s, score, python: row.origins, port: origins, error })
  }
  return scores
}

describe.skipIf(!files.length)('the browser composer', () => {
  const source = createSource(async name => {
    if (!existsSync(join(folder, name))) return null
    const bytes = readFileSync(join(folder, name))
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
  })
  for (const [k, name] of files.entries()) {
    it(`draws ${name} as the Python composer does${k ? '' : ` (mean IoU ≥ ${FLOOR})`}`, async () => {
      const parity = JSON.parse(readFileSync(join(folder, name), 'utf8'))
      const scores = await measure(source, parity)
      const mean = scores.reduce((s, r) => s + r.score, 0) / scores.length
      const worst = scores.slice().sort((a, b) => a.score - b.score).slice(0, Number(process.env.COMPOSER_PARITY_WORST) || 25)
      console.log(`${name}: ${scores.length} sequences, mean IoU ${mean.toFixed(4)}, `
        + `${scores.filter(r => r.score >= 0.99).length} at ≥ 0.99, ${scores.filter(r => r.score < 0.9).length} below 0.9`)
      for (const r of worst) console.log(`  ${r.score.toFixed(3)} ${r.c} ${r.s}  python: ${r.python.join(', ')}  port: ${r.error ?? r.port.join(', ')}`)
      if (!k) expect(mean).toBeGreaterThanOrEqual(FLOOR)
    }, 900_000)
  }
})
