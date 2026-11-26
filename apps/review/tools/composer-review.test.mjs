// The browser composer's guards, on a small format-7 export built here: `han-compose/constants.json`
// holds the drawing constants, and a few characters are boxes. The layout network is all zeros, so
// two-operand nodes fall back to the composer's shares, which space and fit their parts with masks.
import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import { gzipSync } from 'node:zlib'
import constants from 'han-compose/constants.json' with { type: 'json' }
import { Composer } from 'han-compose/compose'
import { LIMITS, compose, createSource, size, sketch, svgBox, tooLarge } from 'han-compose'
import { createDrawer, createPainter } from '../src/lib/composer/client.js'
import { badge, placeholder } from '../src/lib/composer/preview.js'

const hex = text => createHash('sha256').update(text).digest('hex')
const recordShards = entries => {
  const out = {}
  for (const [text, value] of entries) {
    const h = hex(text)
    ;((out[`g/${h.slice(0, 2)}/${h.slice(0, 4)}.json`] ??= {})[text] = value)
  }
  return Object.fromEntries(Object.entries(out).map(([name, file]) => [name, gzipSync(JSON.stringify(file))]))
}
const int16 = values => Buffer.from(Int16Array.from(values).buffer).toString('base64')
const glyph = boxes => {
  const b = [Math.min(...boxes.map(x => x[0])), Math.min(...boxes.map(x => x[1])), Math.max(...boxes.map(x => x[2])), Math.max(...boxes.map(x => x[3]))]
  return { v: 1, b, p: boxes.map(([x0, y0, x1, y1]) => ['MLLL', int16([x0, y0, x1, y0, x1, y1, x0, y1].map(v => v * 4)), int16(Array(8).fill(0))]) }
}
const character = (key, boxes) => ({ k: key, G: glyph(boxes), ink: boxes.reduce((s, [x0, y0, x1, y1]) => s + 2 * (x1 - x0 + y1 - y0), 0), nl: boxes.length, o: boxes.length })
const ops = ['⿰', '⿱', '⿸', '⿺', '⿵', '⿹', '⿶', '⿴', '⿷']
const layout = { ops, defaults: Object.fromEntries(['L', 'R', 'T', 'B', ...ops.slice(2).flatMap(op => [`${op}0`, `${op}1`])].map(p => [p, [0, 0, 1000, 1000]])),
  features: {}, seeds: [[{ shape: [ops.length + 2 * 81, 8], data: Buffer.alloc(2 * (ops.length + 162) * 8).toString('base64') }, Buffer.alloc(32).toString('base64')]] }
const records = {
  木: character('木', [[100, -60, 900, 0], [470, -80, 530, 820]]),
  口: character('口', [[120, -40, 880, 20], [120, 700, 880, 760], [120, -40, 180, 760], [820, -40, 880, 760]]),
  永: character('永', [[100, 0, 900, 800]]),
}
const files = {
  'manifest.json': JSON.stringify({ format: 7, files: { layout: { path: 'layout.json' }, identity: { path: 'identity.json' } }, constants, spelled: [] }),
  'identity.json': JSON.stringify({ s: {}, e: {} }),
  'layout.json': JSON.stringify(layout),
  ...recordShards(Object.entries(records)),
}
const sourceOf = (fetched = []) => createSource(async name => {
  fetched.push(name)
  const body = files[name]
  return body === undefined ? null : new Uint8Array(Buffer.from(body)).buffer
})
const SEQUENCES = ['⿰木口', '⿱口木', '⿴口木', '⿲木口木', '⿱⿰木木口', '⿾⿰木口', '㇯口木', '⿼口木']

describe('the composer', () => {
  it('draws the same whether its files arrive at once or as it asks for them', async () => {
    const lazy = sourceOf(), eager = sourceOf()
    await eager.ready()
    await eager.load(Object.keys(files).filter(name => name.includes('/')))
    for (const sequence of SEQUENCES) {
      const a = await compose(lazy, sequence), b = await compose(eager, sequence)
      expect(a.d.length).toBeGreaterThan(20)
      expect(a).toEqual(b)
    }
  })
  it('names the operators it draws beyond the composer, and credits KanjiVG only for its pieces', async () => {
    const source = sourceOf()
    expect((await compose(source, '⿾⿰木口')).approximate).toEqual(['⿾'])
    expect((await compose(source, '㇯⿼口木木')).approximate.toSorted()).toEqual(['㇯', '⿼'].toSorted())
    const plain = await compose(source, '⿰木口')
    expect(plain.approximate).toEqual([])
    expect(plain.kanjivg).toBe(false)
  })
  it('draws nothing larger than its limits', async () => {
    const chain = n => '⿰木'.repeat(n) + '口'
    const nest = n => n ? ['⿰', '木', nest(n - 1)] : '口'
    expect(LIMITS).toEqual({ nodes: 24, depth: 8 })
    expect(size({ op: '⿰', parts: ['木', { op: '⿱', parts: ['口', null] }] })).toEqual({ nodes: 2, depth: 2 })
    expect(tooLarge(nest(8))).toBe(false)
    expect(tooLarge(nest(9))).toBe(true)
    await expect(compose(sourceOf(), chain(9))).rejects.toThrow('larger than a preview draws')
  })
  it('fails when its manifest is missing or a file cannot be fetched', async () => {
    await expect(compose(createSource(async () => { throw new Error('manifest.json: 404') }), '⿰木口')).rejects.toThrow('404')
    const broken = createSource(async name => name === 'manifest.json' || name === 'layout.json' || name === 'identity.json' ? new Uint8Array(Buffer.from(files[name])).buffer : Promise.reject(new Error('network down')))
    await expect(compose(broken, '⿰木口')).rejects.toThrow('network down')
  })
})

class FakeWorker {
  static made = []
  constructor() { this.posted = []; this.ended = false; FakeWorker.made.push(this) }
  postMessage(message) { this.posted.push(message) }
  terminate() { this.ended = true }
  answer(data) { this.onmessage?.({ data }) }
}

describe('the drawer and painter', () => {
  it('ends the worker of a drawing a newer sequence replaces, and drops its late answer', async () => {
    FakeWorker.made = []
    const drawer = createDrawer(() => new FakeWorker(), 'https://glyphatlas.org/atlas/composer/v/')
    const first = drawer.draw('⿰木口')
    const [one] = FakeWorker.made
    expect(one.posted[0]).toMatchObject({ sequence: '⿰木口', base: 'https://glyphatlas.org/atlas/composer/v/' })
    const second = drawer.draw('⿱木口')
    expect(await first).toEqual({ status: 'cancelled' })
    expect(one.ended).toBe(true)
    const two = FakeWorker.made[1]
    one.answer({ id: one.posted[0].id, drawn: { d: 'M0 0Z' } })
    two.answer({ id: two.posted[0].id, drawn: { d: 'M1 1Z' } })
    expect(await second).toEqual({ status: 'composed', drawn: { d: 'M1 1Z' }, sketch: null })
  })
  it('queues painted drawings on one worker without cancelling earlier ones', async () => {
    FakeWorker.made = []
    const loaders = { 'sources/bopomofo/outlines.json.gz': 'https://glyphatlas.org/composer-data/sources/bopomofo/outlines.json.gz' }
    const painter = createPainter(() => new FakeWorker(), 'https://glyphatlas.org/atlas/composer/v', { loaders })
    const a = painter.paint('⿰木口'), b = painter.paint('⿱木口')
    expect(FakeWorker.made).toHaveLength(1)
    const [w] = FakeWorker.made
    expect(w.posted[0]).toMatchObject({ sequence: '⿰木口', base: 'https://glyphatlas.org/atlas/composer/v/', loaders })
    w.answer({ id: w.posted[0].id, sketch: [] })
    w.answer({ id: w.posted[1].id, drawn: { d: 'M2 2Z' } })
    w.answer({ id: w.posted[0].id, drawn: { d: 'M1 1Z' } })
    expect(await a).toEqual({ d: 'M1 1Z' })
    expect(await b).toEqual({ d: 'M2 2Z' })
  })
})

describe('the preview badge and placeholder', () => {
  it('says preview, approximate, final, failed or too large', () => {
    expect(badge('loading')).toEqual({ label: 'draft', params: {} })
    expect(badge('capped')).toEqual({ label: 'tooLarge', params: {} })
    expect(badge('failed')).toEqual({ label: 'failed', params: {} })
    expect(badge('composed', { approximate: [] })).toEqual({ label: 'draft', params: {} })
    expect(badge('composed', { approximate: ['⿾', '㇯'] })).toEqual({ label: 'approximate', params: { ops: '⿾ ㇯' } })
    expect(badge('final')).toEqual({ label: 'final', params: {} })
  })
  it('is drawn as a rounded box inset by half a stroke, its dots a stroke apart', () => {
    const p = placeholder([100, 100, 400, 800], 80)
    expect(p).toMatchObject({ x: 140, y: 140, width: 320, height: 720 })
    expect(p.gap).toBeGreaterThan(p.dot)
  })
})

describe('a part with a variation selector', () => {
  const key = '口\u{E0101}'
  const crossed = [[120, -40, 180, 760], [820, -40, 880, 760], [120, 700, 880, 760], [0, 300, 300, 420]]
  const cut = (h, boxes) => { const { G, ink } = character(h, boxes); return { h, op: '⿰', ok: ['木'], si: ink, d: 0, j: 1, v: 1, b: G.b, p: G.p } }
  const hosts = { 'R\t口': [cut('杏', [[120, -40, 880, 20], [120, 700, 880, 760], [120, -40, 180, 760], [820, -40, 880, 760]]), cut('可', crossed)] }
  const outline = character(key, crossed)
  const base = { ...records.口, f: [1, 2], s: '⿱一一', P: ['R'] }
  const composer = new Composer({ manifest: { constants, spelled: [] }, layout, rec: k => k === '口' ? base : k === key ? outline : records[k] ?? null,
    hosts: (k, place) => hosts[`${place}\t${k}`] ?? [], places: () => [] })

  it('draws from its own record and takes no host cut of its base', () => {
    expect(composer.rec(key)).toMatchObject({ G: outline.G, o: 4 })
    expect(composer.hostsOf('口', 'R').map(h => h.h)).toEqual(['杏', '可'])
    expect(composer.hostsOf(key, 'R')).toEqual([])
  })
})
