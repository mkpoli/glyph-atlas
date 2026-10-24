import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import { drawn, loadDrawing } from '../src/lib/ids.js'

// A stand-in for the browser's image: a drawing loads unless its URL is listed as missing.
const requested = [], missing = new Set()
globalThis.Image = class {
  set src(url) {
    requested.push(url)
    queueMicrotask(() => missing.has(url) ? this.onerror?.() : this.onload?.())
  }
}
const address = text => `/atlas/ids/${createHash('sha256').update(text.normalize('NFC')).digest('hex')}.svg`

describe('a description drawing', () => {
  it('is served under the sha256 of the NFC text and loads once a page', async () => {
    expect(drawn('⿰亻⿱七木')).toBeUndefined()
    const [first, second] = await Promise.all([loadDrawing('⿰亻⿱七木'), loadDrawing('⿰亻⿱七木')])
    expect(first).toBe(address('⿰亻⿱七木'))
    expect(second).toBe(first)
    expect(await loadDrawing('⿰亻⿱七木')).toBe(first)
    expect(drawn('⿰亻⿱七木')).toBe(first)
    expect(requested.filter(url => url === first)).toHaveLength(1)
  })
  it('is null where none is made, and is not asked for again', async () => {
    missing.add(address('⿱龴疋'))
    expect(await loadDrawing('⿱龴疋')).toBeNull()
    expect(await loadDrawing('⿱龴疋')).toBeNull()
    expect(drawn('⿱龴疋')).toBeNull()
    expect(requested.filter(url => url === address('⿱龴疋'))).toHaveLength(1)
  })
})
