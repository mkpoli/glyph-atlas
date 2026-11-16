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

// The rules of a size: each `[gallery width it starts at, columns]`. The default size is the root that names no other.
const rulesOf = (style, size) => {
  const root = size === ZOOM ? ':root:not\\([^)]*\\)' : `:root\\[data-tile-size="${size}"\\]`
  const rules = [...style.matchAll(new RegExp(`min-width:(\\d+)px\\)\\{${root} \\.collage-block\\{--collage-cols:(\\d+)`, 'g'))].map(match => [Number(match[1]), Number(match[2])])
  expect(rules.length).toBeGreaterThan(0)
  return rules
}
const most = (style, size) => Math.max(...rulesOf(style, size).map(([, columns]) => columns))
const fit = (style, size, width) => Math.max(2, ...rulesOf(style, size).filter(([from]) => from <= width).map(([, columns]) => columns))

describe('the tile sizes', () => {
  const style = collageStyle()
  it('offer sizes below the old smallest, the default among them', () => {
    expect(ZOOMS[0]).toBeLessThan(70)
    expect(ZOOMS).toContain(ZOOM)
    expect(ZOOMS).toEqual([...ZOOMS].sort((a, b) => a - b))
  })
  it('stop at ten columns from the default up and go on below it', () => {
    for (const size of ZOOMS.filter(size => size >= ZOOM)) expect(most(style, size)).toBe(10)
    for (const size of ZOOMS.filter(size => size < ZOOM)) expect(most(style, size)).toBe(COLUMNS.at(-1))
  })
  it('never take fewer columns at a smaller size', () => {
    for (const width of [356, 912, 1313, 1751, 2189, 3502]) {
      expect(fit(style, ZOOMS[0], width)).toBeGreaterThan(2)
      ZOOMS.slice(1).forEach((size, i) => expect(fit(style, ZOOMS[i], width)).toBeGreaterThanOrEqual(fit(style, size, width)))
    }
  })
  it('leave a tile too narrow for a caption bare, by rules that outweigh the stylesheet\'s own', () => {
    for (const size of ZOOMS) expect(style.includes(`:root[data-tile-size="${size}"] .glyph-grid.collage .collage-cell :is(.tile-caption,.tile-details,.tile-select){display:none}`)).toBe(size < BARE)
  })
})
