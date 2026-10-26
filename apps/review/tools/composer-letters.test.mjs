// Letters drawn by the browser composer from a han-compose export that records them
// (`compose_ids.py export-web OUT --add letters`, or any export made since). COMPOSER_EXPORT names the
// export's folder; without it, or when the export holds no record for K, the test is skipped.
import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { gunzipSync } from 'node:zlib'
import { compose, createSource } from '../src/lib/composer/index.js'

const folder = process.env.COMPOSER_EXPORT
const hex = text => createHash('sha256').update(text).digest('hex')
const recorded = key => {
  const shard = folder && join(folder, 'g', `${hex(key).slice(0, 3)}.json`)
  return Boolean(shard && existsSync(shard) && JSON.parse(gunzipSync(readFileSync(shard)).toString())[hex(key)])
}
const letters = recorded('K') && recorded('O')
if (!letters) console.log('composer letters skipped: set COMPOSER_EXPORT to a han-compose export that records K and O')

describe.skipIf(!letters)('letters in the browser composer', () => {
  const source = createSource(async name => {
    if (!existsSync(join(folder, name))) return null
    const bytes = readFileSync(join(folder, name))
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
  })
  it('draws a Latin leaf from its own glyph', async () => {
    const drawn = await compose(source, '⿸广⿰KO')
    expect(drawn.origins.filter(origin => /^glyph [KO]$/.test(origin))).toEqual(['glyph K', 'glyph O'])
    expect(drawn.d.length).toBeGreaterThan(100)
  })
  it('draws kana and Hangul the same way', async () => {
    for (const [sequence, leaf] of [['⿸广あ', 'あ'], ['⿸广가', '가'], ['⿸广Ж', 'Ж']]) {
      if (!recorded(leaf)) continue
      expect((await compose(source, sequence)).origins).toContain(`glyph ${leaf}`)
    }
  })
})
