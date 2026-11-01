// The collage: a gallery's tiles in columns of one width, each tile as tall as its crop's proportions
// make it, placed in reading order at the top of the shortest column, as a pinboard is.
//
// The place of every tile is worked out here for each column count the stylesheet may choose
// (`COLUMNS`), as numbers the stylesheet turns into lengths: a tile's column, the height of the crops
// above it as a share of their width, and how many tiles stand above it. The page therefore lays the
// collage out at its final size from its first paint, before any image arrives and before any script
// has measured it, and a resize only picks another set. A crop's proportions are its recorded image
// size or box (`cropShape`); a record that names neither is square. Tiles added at the end of a
// gallery leave every tile before them where it was.
import { cropShape } from './cropPaint.js'

/** The most columns a gallery uses: a few large tiles read better than many small ones they scroll past. */
const WIDEST = 10
/** The column counts the stylesheet chooses between by the gallery's width and the tile size. */
export const COLUMNS = Array.from({ length: WIDEST - 1 }, (_, i) => i + 2)
/** The tile sizes the reader chooses between, smallest first: the narrowest a column may be, in CSS pixels.
 *  A size past the widest column count is reached by screen width alone, at the same tile width. */
export const ZOOMS = [70, 80, 90, 100, 130, 170, 220]
/** The tile size a reader who has not chosen one sees, in the middle of the choice. */
export const ZOOM = 100
/** The size of a tile on a wide screen that has not been scaled: the size the sixth column in a full-width
 *  gallery takes at the default tile size (columns the reader can ask for grew wider at the widths beyond
 *  the tenth column, and a wide screen should not pay for that). Images are shown up to this multiple. */
const WIDE = 'clamp(166px, 12.6vw, 440px)'
/** The gap between columns once the gallery is wide enough for three. */
const GAP = 12
/** How far a crop's height may run from its width: a very flat or very tall crop is shown in a tile at this limit. */
const FLATTEST = 0.55, TALLEST = 1.9
/** The caption and gap as a share of a typical column's width, for choosing the shortest column. */
const CHROME = 0.28

const round = n => Math.round(n * 1000) / 1000

/** A tile's height as a share of its image's width. */
export function tileAspect(item) {
  const shape = item?.aspect ?? (s => s && s[1] / s[0])(cropShape(item))
  return round(Math.min(TALLEST, Math.max(FLATTEST, shape > 0 ? shape : 1)))
}

/**
 * The collage of a list of tiles' aspects (height over width): for each tile its inline style, and the
 * block's own, which holds its height for each column count.
 */
export function collage(aspects) {
  const places = aspects.map(a => [`--cell-a:${a}`])
  const heights = []
  for (const count of COLUMNS) {
    const columns = Array.from({ length: count }, () => ({ y: 0, n: 0 }))
    aspects.forEach((a, i) => {
      let at = 0
      for (let c = 1; c < count; c++) if (columns[c].y + CHROME * columns[c].n < columns[at].y + CHROME * columns[at].n - 1e-9) at = c
      const column = columns[at]
      places[i].push(`--cell-c${count}:${at}`, `--cell-y${count}:${round(column.y)}`, `--cell-n${count}:${column.n}`)
      column.y += a; column.n += 1
    })
    // The block is as tall as its tallest column, whichever that is at the width it is shown at.
    const used = columns.filter(column => column.n)
    heights.push(`--collage-h${count}:${used.length ? `max(${used.map(column => `calc(${round(column.y)} * var(--collage-iw) + ${column.n} * var(--collage-step))`).join(',')})` : '0px'}`)
  }
  return { tiles: places.map(place => place.join(';')), block: heights.join(';') }
}

/**
 * The stylesheet that picks the column count: for each tile size, as many columns as fit at that
 * width or wider (`--tile-w`, which the grid of squares uses too). The size is the document's
 * `data-tile-size`, a width of `ZOOMS`; a document without one, or with another value, takes `ZOOM`.
 * The rules query the gallery by its container name (`app.css`), being inside the block they style.
 * Past `WIDEST` columns a gallery stops adding them and the tiles grow to fill the width instead: the
 * default, in `WIDE`; another size, to the width the smallest size already gives that column count, so
 * a larger size is still the wider tile.
 */
export function collageStyle() {
  const rules = COLUMNS.flatMap(n => [
    `@property --cell-c${n}{syntax:"<number>";inherits:false;initial-value:0}@property --cell-y${n}{syntax:"<number>";inherits:false;initial-value:0}@property --cell-n${n}{syntax:"<number>";inherits:false;initial-value:0}@property --collage-h${n}{syntax:"*";inherits:false}`])
  const others = ZOOMS.filter(width => width !== ZOOM).map(width => `[data-tile-size="${width}"]`).join(',')
  ZOOMS.forEach(width => {
    const root = width === ZOOM ? `:root:not(${others})` : `:root[data-tile-size="${width}"]`
    const smallest = Math.min(width, ZOOMS[0])
    // The gallery's own width inside the page's padding, which is 4.4vw of the screen either side.
    const beyond = `calc(100vw * .912 / ${WIDEST} + ${GAP}px)`
    const fill = width === ZOOM ? `min(${smallest}px + 4vw - 4px, ${WIDE})` : `clamp(${smallest}px + 4vw - 4px, ${beyond}, ${width}px + 2vw)`
    rules.push(`${root}{--tile-w:${width}px;--tile-fill:${fill}}`)
    for (const n of COLUMNS.slice(1)) rules.push(`@container gallery (min-width:${n * width + (n - 1) * GAP}px){${root} .collage-block{--collage-cols:${n};--collage-h:var(--collage-h${n})}${root} .collage-cell{--cell-c:var(--cell-c${n});--cell-y:var(--cell-y${n});--cell-n:var(--cell-n${n})}}`)
  })
  rules.push(`@container gallery (min-width:${COLUMNS.at(-1) * ZOOMS[0] + (COLUMNS.at(-1) - 1) * GAP}px){:root .collage-block{--collage-cols:${WIDEST};--collage-h:var(--collage-h${WIDEST})}:root .collage-cell{--cell-c:var(--cell-c${WIDEST});--cell-y:var(--cell-y${WIDEST});--cell-n:var(--cell-n${WIDEST})}}`)
  return rules.join('\n')
}

/** Where the reader's choice of tile size is remembered; `app.html` reads it before the first paint. */
export const ZOOM_KEY = 'atlas.tileSize'
/** The tile size the reader chose last, one of `ZOOMS`. */
export function storedZoom() {
  try { const z = JSON.parse(localStorage.getItem(ZOOM_KEY)); return ZOOMS.includes(z) ? z : ZOOM } catch { return ZOOM }
}

/** Where the reader's choice of layout is remembered; `app.html` reads it before the first paint. */
export const LAYOUT_KEY = 'atlas.galleryLayout'
/** The layout the reader chose last: the collage unless they chose the grid. */
export function storedLayout() {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY)) === 'grid' ? 'grid' : 'collage' } catch { return 'collage' }
}
