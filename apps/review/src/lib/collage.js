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

/** The column counts the stylesheet chooses between by the gallery's width. */
export const COLUMNS = [2, 3, 4, 5, 6, 7, 8]
/** How far a crop's height may run from its width: a very flat or very tall crop is shown in a tile at this limit. */
const FLATTEST = 0.55, TALLEST = 1.9
/** A tile's label and footer, as a share of a typical column's width, for choosing the shortest column. */
const CHROME = 0.3

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
  const places = aspects.map(a => [`--a:${a}`])
  const heights = []
  for (const count of COLUMNS) {
    const columns = Array.from({ length: count }, () => ({ y: 0, n: 0 }))
    aspects.forEach((a, i) => {
      let at = 0
      for (let c = 1; c < count; c++) if (columns[c].y + CHROME * columns[c].n < columns[at].y + CHROME * columns[at].n - 1e-9) at = c
      const column = columns[at]
      places[i].push(`--c${count}:${at}`, `--y${count}:${round(column.y)}`, `--n${count}:${column.n}`)
      column.y += a; column.n += 1
    })
    // The block is as tall as its tallest column, whichever that is at the width it is shown at.
    const used = columns.filter(column => column.n)
    heights.push(`--h${count}:${used.length ? `max(${used.map(column => `calc(${round(column.y)} * var(--iw) + ${column.n} * var(--step))`).join(',')})` : '0px'}`)
  }
  return { tiles: places.map(place => place.join(';')), block: heights.join(';') }
}

/** Where the reader's choice of layout is remembered; `app.html` reads it before the first paint. */
export const LAYOUT_KEY = 'atlas.galleryLayout'
/** The layout the reader chose last: the collage unless they chose the grid. */
export function storedLayout() {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY)) === 'grid' ? 'grid' : 'collage' } catch { return 'collage' }
}
