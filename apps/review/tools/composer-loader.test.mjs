import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import { gzipSync } from 'node:zlib'
import constants from 'han-compose/constants.json' with { type: 'json' }
import { fromUrl } from 'han-compose'

// A format-7 export over HTTP as the Worker serves it: record shards live in `g/<hh>/<hhhh>.json`
// and host-list shards in `p/<hhh>.json`, gzip bytes under a .json name, each an object keyed by
// the key itself (`place\toperand` for host lists); a file the export lacks is a 404.
const hex = text => createHash('sha256').update(text).digest('hex')
const recordShard = key => { const h = hex(key); return `g/${h.slice(0, 2)}/${h.slice(0, 4)}.json` }
const hostShard = key => `p/${hex(key).slice(0, 3)}.json`
const half = n => Buffer.alloc(2 * n).toString('base64')
const manifest = format => Buffer.from(JSON.stringify({ format, files: { layout: { path: 'layout.json' }, identity: { path: 'identity.json' } }, constants }))
const files = {
  'manifest.json': manifest(7),
  'identity.json': Buffer.from(JSON.stringify({ s: {}, e: {} })),
  'layout.json': Buffer.from(JSON.stringify({ ops: ['⿰'], defaults: {}, features: {}, seeds: [[{ shape: [23, 8], data: half(23 * 8) }, Buffer.alloc(32).toString('base64')]] })),
  [recordShard('木')]: gzipSync(JSON.stringify({ 木: { k: '木', ink: 3000, P: ['L'] } })),
  [hostShard('L\t木')]: gzipSync(JSON.stringify({ 'L\t木': [{ h: '林', r: 3, c: null }, { h: '村', r: 1, c: { k: [0, 2], b: [10, 20, 300, 800] } }] })),
}
const served = (table, asked = []) => async url => {
  const name = url.replace('/atlas/composer/v/', '')
  asked.push(name)
  const body = table[name]
  return body ? new Response(body, { headers: { 'content-type': name.includes('/') ? 'application/octet-stream' : 'application/json' } })
    : new Response('{}', { status: 404 })
}

describe('the composer loader', () => {
  it('reads gzipped shards served as bytes, a key absent from its shard or a missing shard as empty, each shard once', async () => {
    const asked = []
    const source = fromUrl('/atlas/composer/v/', { fetch: served(files, asked) })
    await source.ready()
    await source.load(source.filesOf(['木', '口']))
    await source.load([hostShard('L\t木'), hostShard('R\t木')])
    const store = source.store()
    expect(store.rec('木')).toEqual({ k: '木', ink: 3000, P: ['L'] })
    expect(store.rec('口')).toBeNull()
    expect(store.places('木')).toEqual(['L'])
    expect(store.places('口')).toEqual([])
    expect(store.hosts('木', 'L').map(h => h.h)).toEqual(['村', '林'])
    expect(store.hosts('木', 'R')).toEqual([])
    expect(store.missing.size).toBe(0)
    expect(store.hosts('口', 'L')).toEqual([])
    expect([...store.missing]).toEqual([hostShard('L\t口')])
    await source.load(source.filesOf(['木']))
    expect(asked.filter(name => name === recordShard('木'))).toHaveLength(1)
  })

  it('reads an uncut entry as having no cut, and a cut as the host contours it keeps with their box', async () => {
    const source = fromUrl('/atlas/composer/v/', { fetch: served(files) })
    await source.ready()
    await source.load([hostShard('L\t木')])
    const [kept, uncut] = source.store().hosts('木', 'L')
    expect(kept).toMatchObject({ h: '村', k: [0, 2], b: [10, 20, 300, 800], c: true })
    expect(uncut).toMatchObject({ h: '林', c: null })
    expect(uncut.k).toBeUndefined()
  })

  it('refuses an export of any other format', async () => {
    for (const format of [1, 2, 3, 4, 5, 6]) {
      const source = fromUrl('/atlas/composer/v/', { fetch: served({ ...files, 'manifest.json': manifest(format) }) })
      await expect(source.ready()).rejects.toThrow(`format ${format} is not read here`)
    }
  })
})
