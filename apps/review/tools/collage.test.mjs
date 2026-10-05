import { describe, expect, it } from 'bun:test'
import { COLUMNS, collage, tileAspect } from '../src/lib/collage.js'

const place = (style, count) => Object.fromEntries(style.split(';').map(part => part.split(':')).filter(([key]) => [`--cell-c${count}`, `--cell-y${count}`, `--cell-n${count}`].includes(key)).map(([key, value]) => [key.slice(7, 8), Number(value)]))

describe('the collage', () => {
  it('sizes a tile by its image, else its box, else as a square', () => {
    expect(tileAspect({ image_size: [40, 60] })).toBe(1.5)
    expect(tileAspect({ image: 'https://example.org/iiif/x/0/default.jpg', box: { x: 0, y: 0, w: 50, h: 40 } })).toBe(0.8)
    expect(tileAspect({})).toBe(1)
  })
  it('keeps a very flat or very tall crop within limits', () => {
    expect(tileAspect({ image_size: [100, 10] })).toBe(0.55)
    expect(tileAspect({ image_size: [10, 100] })).toBe(1.9)
  })
  it('places each tile at the top of the shortest column, in order', () => {
    const { tiles } = collage([1, 2, 1, 1])
    expect(tiles.map(style => place(style, 2))).toEqual([
      { c: 0, y: 0, n: 0 }, { c: 1, y: 0, n: 0 }, { c: 0, y: 1, n: 1 }, { c: 1, y: 2, n: 1 },
    ])
  })
  it('leaves earlier tiles where they were when more are added', () => {
    const aspects = [1, 1.4, 0.8, 1.2, 1.6, 0.9, 1.1]
    const before = collage(aspects).tiles, after = collage([...aspects, 1.3, 0.7]).tiles
    expect(after.slice(0, aspects.length)).toEqual(before)
  })
  it('gives the block a height for every column count', () => {
    const { block } = collage([1, 1])
    for (const count of COLUMNS) expect(block).toContain(`--collage-h${count}:`)
    expect(collage([]).block).toContain('--collage-h2:0px')
  })
})
