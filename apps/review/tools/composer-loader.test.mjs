import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import { gzipSync } from 'node:zlib'
import { fromUrl } from '../src/lib/composer/index.js'

// A format-5 export over HTTP as the Worker serves it: record and host-list shards are gzip bytes
// under a .json name, sent as application/octet-stream, each an object from the full SHA-256 to the
// record or list; a file the export lacks is a 404.
const hex = text => createHash('sha256').update(text).digest('hex')
const shard = (folder, text) => `${folder}/${hex(text).slice(0, 3)}.json`
const half = n => Buffer.alloc(2 * n).toString('base64')
const manifest = format => Buffer.from(JSON.stringify({ format, files: { layout: { path: 'layout.json' } }, constants: {} }))
const files = {
  'manifest.json': manifest(5),
  'layout.json': Buffer.from(JSON.stringify({ ops: ['⿰'], defaults: {}, features: {}, seeds: [[{ shape: [23, 8], data: half(23 * 8) }, Buffer.alloc(32).toString('base64')]] })),
  [shard('g', '木')]: gzipSync(JSON.stringify({ [hex('木')]: { k: '木', ink: 3000, P: ['L'] } })),
  [shard('p', 'L\t木')]: gzipSync(JSON.stringify({ [hex('L\t木')]: { place: 'L', operand: '木', hosts: [{ h: '林', r: 3, c: null }, { h: '村', r: 1, c: { k: [0, 2], b: [10, 20, 300, 800] } }] } })),
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
    await source.load([shard('p', 'L\t木'), shard('p', 'R\t木')])
    const store = source.store()
    expect(store.rec('木')).toEqual({ k: '木', ink: 3000, P: ['L'] })
    expect(store.rec('口')).toBeNull()
    expect(store.places('木')).toEqual(['L'])
    expect(store.places('口')).toEqual([])
    expect(store.hosts('木', 'L').map(h => h.h)).toEqual(['村', '林'])
    expect(store.hosts('木', 'R')).toEqual([])
    expect(store.missing.size).toBe(0)
    expect(store.hosts('口', 'L')).toEqual([])
    expect([...store.missing]).toEqual([shard('p', 'L\t口')])
    await source.load(source.filesOf(['木']))
    expect(asked.filter(name => name === shard('g', '木'))).toHaveLength(1)
  })

  it('reads an uncut entry as having no cut, and a cut as the host contours it keeps with their box', async () => {
    const source = fromUrl('/atlas/composer/v/', { fetch: served(files) })
    await source.ready()
    await source.load([shard('p', 'L\t木')])
    const [kept, uncut] = source.store().hosts('木', 'L')
    expect(kept).toMatchObject({ h: '村', k: [0, 2], b: [10, 20, 300, 800], c: true })
    expect(uncut).toMatchObject({ h: '林', c: null })
    expect(uncut.k).toBeUndefined()
  })

  it('refuses an export of any other format', async () => {
    for (const format of [1, 2, 3, 4]) {
      const source = fromUrl('/atlas/composer/v/', { fetch: served({ ...files, 'manifest.json': manifest(format) }) })
      await expect(source.ready()).rejects.toThrow(`format ${format} is not read here`)
    }
  })
})
