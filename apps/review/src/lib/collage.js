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

/** The most columns a gallery uses at the default tile size or a larger one: a few large tiles read
 *  better than many small ones they scroll past, so past it the tiles grow with the gallery instead. */
const WIDEST = 10
/** The column counts past it, which only a tile size below the default takes. Each is about a seventh
 *  more than the last, so a column is never much wider than the size asked for. */
const FINER = [12, 14, 16, 18, 20, 23, 26, 30, 34, 39, 44, 50]
/** The column counts the stylesheet chooses between by the gallery's width and the tile size. */
export const COLUMNS = [...Array.from({ length: WIDEST - 1 }, (_, i) => i + 2), ...FINER]
/** The tile sizes the reader chooses between, smallest first: the narrowest a column may be, in CSS pixels. */
export const ZOOMS = [40, 55, 70, 80, 90, 100, 130, 170, 220]
/** The tile size a reader who has not chosen one sees. */
export const ZOOM = 100
/** A tile narrower than this is bare: it has no room for a caption, so it is its image alone, close to
 *  the next, and names itself to a pointer that rests on it. */
export const BARE = 70
/** The gap between columns once the gallery is wide enough for three, and between bare tiles. */
const GAP = 12, TIGHT = 4
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
    // The block is as tall as its tallest column, whichever that is at the width it is shown at. Of the
    // columns that hold as many tiles, only the one with the tallest crops can be it.
    const tallest = new Map()
    for (const column of columns) if (column.n) tallest.set(column.n, Math.max(tallest.get(column.n) ?? 0, column.y))
    heights.push(`--collage-h${count}:${tallest.size ? `max(${[...tallest].map(([n, y]) => `calc(${round(y)} * var(--collage-iw) + ${n} * var(--collage-step))`).join(',')})` : '0px'}`)
  }
  return { tiles: places.map(place => place.join(';')), block: heights.join(';') }
}

/**
 * The stylesheet that picks the column count: for each tile size, as many columns as fit at that
 * width or wider (`--tile-w`, which the grid of squares uses too). The size is the document's
 * `data-tile-size`, a width of `ZOOMS`; a document without one, or with another value, takes `ZOOM`.
 * The rules query the gallery by its container name (`app.css`), being inside the block they style.
 * From the default size up a gallery stops at `WIDEST` columns and its tiles grow to fill the width;
 * a smaller size goes on through `FINER`. A size below `BARE` also drops the captions and the hover
 * details, closes the gaps, and in the grid of squares leaves the square to the image.
 */
export function collageStyle() {
  const rules = COLUMNS.flatMap(n => [
    `@property --cell-c${n}{syntax:"<number>";inherits:false;initial-value:0}@property --cell-y${n}{syntax:"<number>";inherits:false;initial-value:0}@property --cell-n${n}{syntax:"<number>";inherits:false;initial-value:0}@property --collage-h${n}{syntax:"*";inherits:false}`])
  const others = ZOOMS.filter(width => width !== ZOOM).map(width => `[data-tile-size="${width}"]`).join(',')
  rules.push(`:root{--tile-w:${ZOOM}px}`)
  ZOOMS.forEach(width => {
    const root = width === ZOOM ? `:root:not(${others})` : `:root[data-tile-size="${width}"]`
    const bare = width < BARE, gap = bare ? TIGHT : GAP, gallery = `${root} .glyph-grid.collage`
    if (width !== ZOOM) rules.push(`${root}{--tile-w:${width}px}`)
    if (bare) rules.push(`${gallery} .collage-block{--collage-gap:${TIGHT}px;--collage-caption:0px}${gallery} :is(.tile-caption,.tile-details){display:none}`,
      `${root}[data-gallery="grid"] .glyph-grid.collage .collage-cell{aspect-ratio:1;padding:3px}`)
    for (const n of COLUMNS.slice(1)) {
      if (n > WIDEST && width >= ZOOM) break
      rules.push(`@container gallery (min-width:${n * width + (n - 1) * gap}px){${root} .collage-block{--collage-cols:${n};--collage-h:var(--collage-h${n})}${root} .collage-cell{--cell-c:var(--cell-c${n});--cell-y:var(--cell-y${n});--cell-n:var(--cell-n${n})}}`)
    }
  })
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
