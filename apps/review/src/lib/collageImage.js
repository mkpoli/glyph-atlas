// A gallery's crops as one image: the collage without its captions, block under block as the gallery
// shows them, over a credit that names the page it was made from and the holders of the images.
//
// The sheet has the gallery's own number of columns, so the reader shapes it with the tile size. A crop
// is drawn as its tile draws it: contained in a box of the tile's proportions (`tileAspect`). The crops are as wide as the sheet's limits allow, never wider than `CELL`: a
// canvas past `AREA` pixels or `SIDE` on a side is one some browsers refuse to make.
import { tileAspect } from './collage.js'

/** The widest and the narrowest a crop is drawn, the gap between crops and the margin of the sheet, in pixels. */
const CELL = 160, NARROWEST = 16, GAP = 4, MARGIN = 24
const AREA = 16_000_000, SIDE = 16000
/** How many crop images are fetched at once. */
const TOGETHER = 6

/** The size of the sheet's text for a sheet `width` pixels wide. */
const typeSize = width => Math.round(Math.min(20, Math.max(12, width / 100)))

/**
 * The places of the crops of `blocks`, each `{ heading, aspects }`, in `count` columns `cell` pixels
 * wide: a block's crops each at the top of its shortest column, in order, and the blocks one under
 * another, each below its heading. Returns the sheet's `width`, the `height` the blocks reach, the
 * `type` size, and per block its heading's baseline `y` (when it has one) and its crops' `cells`.
 */
export function place(blocks, count, cell) {
  const width = 2 * MARGIN + count * cell + (count - 1) * GAP, type = typeSize(width)
  let top = MARGIN
  const placed = blocks.map(block => {
    const heading = block.heading ? top + type : null
    if (block.heading) top += Math.round(type * 1.9)
    const columns = Array.from({ length: count }, () => top)
    const cells = block.aspects.map(aspect => {
      let at = 0
      for (let c = 1; c < count; c++) if (columns[c] < columns[at]) at = c
      const height = Math.round(aspect * cell), box = { x: MARGIN + at * (cell + GAP), y: columns[at], w: cell, h: height }
      columns[at] += height + GAP
      return box
    })
    top = Math.max(top, ...columns) + (block.aspects.length ? GAP * 3 : 0)
    return { heading, cells }
  })
  return { width, height: top, type, blocks: placed }
}

/** `parts` in lines no wider than `width`, joined by ` · `; `measure` gives a text's width. A part wider than a line keeps one to itself. */
export function wrap(parts, width, measure) {
  const lines = []
  for (const part of parts) {
    const joined = lines.length ? `${lines.at(-1)} · ${part}` : part
    if (lines.length && measure(joined) <= width) lines[lines.length - 1] = joined
    else lines.push(part)
  }
  return lines
}

/**
 * The sheet for `blocks` in `count` columns under the `credit` lines (arrays of parts), with the widest
 * crops its limits allow. `measure(text, size)` gives a text's width at a type size.
 */
export function sheet(blocks, count, credit, measure) {
  for (let cell = CELL; ; cell = Math.max(NARROWEST, Math.floor(cell * 0.92))) {
    const placed = place(blocks, count, cell), leading = Math.round(placed.type * 1.6)
    const lines = credit.flatMap(parts => wrap(parts, placed.width - 2 * MARGIN, text => measure(text, placed.type)))
    const height = placed.height + lines.length * leading + MARGIN
    if (cell === NARROWEST || placed.width <= SIDE && height <= SIDE && placed.width * height <= AREA) {
      return { ...placed, height, lines, leading, credit: placed.height + placed.type }
    }
  }
}

/**
 * Paint the crops of `blocks`, each `{ heading, items }`, into one image and answer it as a JPEG blob
 * with how many crop images could not be read (their places stay empty).
 *
 * `count` is the gallery's columns; `square` draws every crop in a square, as the grid of squares
 * does; `credit` is the lines under the crops, drawn in `quiet`; `paper`, `ink` and `font` are the page's
 * own; `filter` is the canvas filter the crops are shown through. `onprogress(done, total)` follows the images in,
 * and `signal` gives the work up.
 */
export async function paint(blocks, { count, square = false, credit = [], paper = '#fff', ink = '#000', quiet = ink, font = 'sans-serif', filter = '', onprogress = () => {}, signal } = {}) {
  const canvas = document.createElement('canvas'), context = canvas.getContext('2d')
  const type = size => `${size}px ${font}`
  const plan = sheet(blocks.map(block => ({ heading: block.heading, aspects: block.items.map(item => square ? 1 : tileAspect(item)) })), count, credit,
    (text, size) => { context.font = type(size); return context.measureText(text).width })
  canvas.width = plan.width; canvas.height = plan.height
  context.fillStyle = paper; context.fillRect(0, 0, plan.width, plan.height)
  context.fillStyle = ink; context.font = type(plan.type); context.textBaseline = 'alphabetic'
  plan.blocks.forEach((block, i) => { if (block.heading != null) context.fillText(blocks[i].heading, MARGIN, block.heading) })
  context.fillStyle = quiet
  plan.lines.forEach((line, i) => context.fillText(line, MARGIN, plan.credit + i * plan.leading))

  const jobs = plan.blocks.flatMap((block, i) => block.cells.map((box, at) => ({ box, item: blocks[i].items[at] })))
  let next = 0, done = 0, missing = 0
  const draw = async () => {
    while (next < jobs.length) {
      const { box, item } = jobs[next++]
      signal?.throwIfAborted()
      try {
        const response = await fetch(item.image, { signal })
        if (!response.ok) throw new Error(String(response.status))
        const image = await createImageBitmap(await response.blob())
        const scale = Math.min(box.w / image.width, box.h / image.height), w = image.width * scale, h = image.height * scale
        context.filter = filter || 'none'
        context.drawImage(image, box.x + (box.w - w) / 2, box.y + (box.h - h) / 2, w, h)
        context.filter = 'none'
        image.close()
      } catch (error) {
        if (signal?.aborted) throw error
        missing += 1
      }
      onprogress(++done, jobs.length)
    }
  }
  await Promise.all(Array.from({ length: Math.min(TOGETHER, jobs.length) }, draw))
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.92))
  canvas.width = canvas.height = 0
  if (!blob) throw new Error('The sheet could not be encoded.')
  return { blob, missing, total: jobs.length, width: plan.width, height: plan.height }
}
