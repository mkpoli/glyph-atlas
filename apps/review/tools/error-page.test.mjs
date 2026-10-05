import { describe, expect, it } from 'bun:test'
import { codeCells, errorCase, sampleCrops, suggestion } from '../src/lib/errorPage.js'

describe('the status code', () => {
  it('is drawn as three-by-five digits with a blank column between them', () => {
    const { columns, cells, lit } = codeCells(404)
    expect(columns).toBe(11)
    expect(cells.length).toBe(45)
    expect(lit).toBe(9 + 12 + 9)
    expect(cells.filter(cell => cell.lit).map(cell => cell.slot)).toEqual([...Array(lit).keys()])
    expect(cells.some(cell => cell.column % 4 === 0)).toBe(false)
  })
})

describe('an error', () => {
  it('is told by its status, its route and the connection', () => {
    expect(errorCase({ status: 404 })).toBe('notFound')
    expect(errorCase({ status: 404, routeId: '/[lang=locale]/crop/[id]' })).toBe('crop')
    expect(errorCase({ status: 404, routeId: '/[lang=locale]/corpus/[id]' })).toBe('crop')
    expect(errorCase({ status: 404, routeId: '/[lang=locale]/character/[code]' })).toBe('character')
    expect(errorCase({ status: 404, routeId: '/[lang=locale]/forms/[[family]]' })).toBe('forms')
    expect(errorCase({ status: 503, code: 'busy' })).toBe('busy')
    expect(errorCase({ status: 500 })).toBe('server')
    expect(errorCase({ status: 500, message: 'TypeError: Failed to fetch' })).toBe('offline')
    expect(errorCase({ status: 404, online: false })).toBe('offline')
    expect(errorCase({ status: 403 })).toBe('other')
  })
})

describe('a missing address', () => {
  it('offers the character, sequence or crop it looks like', () => {
    expect(suggestion('/字')).toEqual({ kind: 'character', path: '/character/U+5B57', text: '字' })
    expect(suggestion('/glyph/u+5b57')).toEqual({ kind: 'character', path: '/character/U+5B57', text: '字' })
    expect(suggestion('/' + encodeURIComponent('か\u3099'))).toEqual({ kind: 'character', path: '/character/U+304B-U+3099', text: 'か\u3099' })
    expect(suggestion('/runs/' + encodeURIComponent('申候'))).toEqual({ kind: 'sequence', path: '/sequence/' + encodeURIComponent('申候'), text: '申候' })
    expect(suggestion('/crops/codh:200005598:x:B0001:C0118')).toEqual({ kind: 'crop', path: '/crop/' + encodeURIComponent('codh:200005598:x:B0001:C0118'), text: 'codh:200005598:x:B0001:C0118' })
  })
  it('offers nothing for words, broken escapes or the page already tried', () => {
    for (const path of ['/', '/nope', '/about/team', '/%E0%A4%A', '/U+110000']) expect(suggestion(path)).toBe(null)
    expect(suggestion('/character/U+5B57', 'character')).toBe(null)
    expect(suggestion('/character/u+5b57', 'character')).toEqual({ kind: 'character', path: '/character/U+5B57', text: '字' })
    expect(suggestion('/crop/ar:x:1', 'crop')).toBe(null)
  })
})

describe('the crops', () => {
  const crop = (id, image = `/atlas/media/${id}.webp`) => ({ id, image, label: 'ん' })
  const answers = {
    '/atlas/ngrams/2': { items: [{ text: 'んべ' }, { text: 'しや' }, { text: 'かし' }] },
    'んべ': { items: [{ crops: [crop('a'), crop('b')] }, { crops: [crop('a'), crop('c', null)] }] },
    'しや': { items: [{ crops: [crop('d'), crop('e')] }] },
  }
  const send = async path => {
    const url = new URL(path, 'https://glyphatlas.org')
    const body = url.pathname === '/atlas/runs' ? answers[url.searchParams.get('text')] : answers[url.pathname]
    expect(url.pathname === '/atlas/runs' ? url.searchParams.get('offset') : '0').toBe('0')
    return body ? Response.json(body) : new Response('{}', { status: 500 })
  }
  it('come from the first page of two frequent pairs, each once and with an image', async () => {
    const { crops, runs } = await sampleCrops({ send, random: () => 0.99 })
    expect(runs).toEqual(['んべ', 'しや'])
    expect(crops.map(c => c.id).sort()).toEqual(['a', 'b', 'd', 'e'])
    expect(crops.every(c => c.image)).toBe(true)
  })
  it('leave out a pair that cannot be read', async () => {
    const { crops, runs } = await sampleCrops({ send, random: () => 0 })
    expect(runs).toEqual(['しや'])
    expect(crops.map(c => c.id).sort()).toEqual(['d', 'e'])
  })
})
