import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import { gzipSync } from 'node:zlib'
import { fromUrl } from '../src/lib/composer/index.js'

// A format-2 export over HTTP as the Worker serves it: the record and host files are gzip bytes under
// a .json name, sent as application/octet-stream; a file the export lacks is a 404.
const hex = text => createHash('sha256').update(text).digest('hex')
const at = (folder, text) => `${folder}/${hex(text).slice(0, 2)}/${hex(text)}.json`
const half = n => Buffer.alloc(2 * n).toString('base64')
const files = {
  'manifest.json': Buffer.from(JSON.stringify({ format: 2, files: { layout: { path: 'layout.json' } }, constants: {} })),
  'layout.json': Buffer.from(JSON.stringify({ ops: ['⿰'], defaults: {}, features: {}, seeds: [[{ shape: [23, 8], data: half(23 * 8) }, Buffer.alloc(32).toString('base64')]] })),
  [at('g', '木')]: gzipSync(JSON.stringify({ k: '木', ink: 3000, P: ['L'] })),
  [at('p', 'L\t木')]: gzipSync(JSON.stringify({ place: 'L', operand: '木', hosts: [{ h: '林', r: 3 }, { h: '村', r: 1 }] })),
}
const asked = []
const fetch = async url => {
  const name = url.replace('/atlas/composer/v/', '')
  asked.push(name)
  const body = files[name]
  return body ? new Response(body, { headers: { 'content-type': name.endsWith('.json') && name.includes('/') ? 'application/octet-stream' : 'application/json' } })
    : new Response('{}', { status: 404 })
}

describe('the composer loader', () => {
  it('reads gzipped record and host files served as bytes, a missing one as empty, each once', async () => {
    const source = fromUrl('/atlas/composer/v/', { fetch })
    await source.ready()
    await source.load(source.filesOf(['木', '口']))
    await source.load([at('p', 'L\t木'), at('p', 'R\t木')])
    const store = source.store()
    expect(store.rec('木')).toEqual({ k: '木', ink: 3000, P: ['L'] })
    expect(store.rec('口')).toBeNull()
    expect(store.places('木')).toEqual(['L'])
    expect(store.hosts('木', 'L').map(h => h.h)).toEqual(['村', '林'])
    expect(store.hosts('木', 'R')).toEqual([])
    expect(store.missing.size).toBe(0)
    expect(store.hosts('口', 'L')).toEqual([])
    expect([...store.missing]).toEqual([at('p', 'L\t口')])
    await source.load(source.filesOf(['木']))
    expect(asked.filter(name => name === at('g', '木'))).toHaveLength(1)
  })
})

describe('a format-3 host list', () => {
  it('reads an uncut entry as having no cut, and a cut as the host contours it keeps with their box', async () => {
    const three = { ...files, 'manifest.json': Buffer.from(JSON.stringify({ format: 3, files: { layout: { path: 'layout.json' } }, constants: {} })),
      [at('p', 'L\t木')]: gzipSync(JSON.stringify({ hosts: [{ h: '林', r: 2, c: null }, { h: '村', r: 0, c: { k: [0, 2], b: [10, 20, 300, 800] } }] })) }
    const source = fromUrl('/v3/', { fetch: async url => { const body = three[url.replace('/v3/', '')]; return body ? new Response(body) : new Response('', { status: 404 }) } })
    await source.ready()
    await source.load([at('p', 'L\t木')])
    const [kept, uncut] = source.store().hosts('木', 'L')
    expect(kept).toMatchObject({ h: '村', k: [0, 2], b: [10, 20, 300, 800] })
    expect(uncut.h).toBe('林')
    expect(uncut.k).toBeUndefined()
  })
})
