import { describe, expect, it } from 'bun:test'
import { BARE, COLUMNS, ZOOM, ZOOMS, collage, collageStyle, tileAspect } from '../src/lib/collage.js'

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
  it('names one height for the columns that hold as many tiles', () => {
    const { block } = collage([1, 1.5, 1, 1])
    expect(block).toContain('--collage-h2:max(calc(2.5 * var(--collage-iw) + 2 * var(--collage-step)))')
  })
})

// The most columns a size takes: the widest gallery its rules name.
const most = (style, size) => Math.max(...[...style.matchAll(new RegExp(`\\{:root\\[data-tile-size="${size}"\\] \\.collage-block\\{--collage-cols:(\\d+)`, 'g'))].map(match => Number(match[1])))

describe('the tile sizes', () => {
  const style = collageStyle()
  it('offer sizes below the old smallest, the default among them', () => {
    expect(ZOOMS[0]).toBeLessThan(70)
    expect(ZOOMS).toContain(ZOOM)
    expect(ZOOMS).toEqual([...ZOOMS].sort((a, b) => a - b))
  })
  it('stop at ten columns from the default up and go on below it', () => {
    for (const size of ZOOMS.filter(size => size > ZOOM)) expect(most(style, size)).toBe(10)
    expect(style).toContain('.collage-block{--collage-cols:10;')
    for (const size of ZOOMS.filter(size => size < ZOOM)) expect(most(style, size)).toBe(COLUMNS.at(-1))
  })
  it('never take fewer columns at a smaller size', () => {
    const fit = (size, width) => Math.max(2, ...[...style.matchAll(new RegExp(`min-width:(\\d+)px\\)\\{:root\\[data-tile-size="${size}"\\] \\.collage-block\\{--collage-cols:(\\d+)`, 'g'))].filter(match => Number(match[1]) <= width).map(match => Number(match[2])))
    const sizes = ZOOMS.filter(size => size !== ZOOM)
    for (const width of [356, 912, 1313, 1751, 2189, 3502]) sizes.slice(1).forEach((size, i) => expect(fit(sizes[i], width)).toBeGreaterThanOrEqual(fit(size, width)))
  })
  it('leave a tile too narrow for a caption bare', () => {
    for (const size of ZOOMS) expect(style.includes(`:root[data-tile-size="${size}"] .glyph-grid.collage :is(.tile-caption,.tile-details){display:none}`)).toBe(size < BARE)
  })
})
