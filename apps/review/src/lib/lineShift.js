// A line whose labels sit one box off: the crop shows the character that its neighbour's label names.
// The strip's `items` are the crop and its neighbours with their `offset` from it (0 is the crop) and
// their label. Moving the run by `offset` gives each crop the label of the box `offset` places away.
// The run spans the crop and runs both ways from it until a crop already holds the label it would
// get. A box marked as no character takes no label and the labels carry on past it, so the boxes
// beyond it are one place further along. `skip` and `cut` move the run's start and end, inward when
// positive and outward when negative, past where the labels agreed.

/** The boxes of a shift by `offset`, in reading order: `{ at, box, label, blank, kept }`, and whether
 *  the run can reach further at either end. */
export function planShift(items, { offset, skip = 0, cut = 0, blanks = new Set() }) {
  const by = new Map(items.map(item => [item.offset, item]))
  const make = (at, source) => {
    const box = by.get(at), kept = box.state === 'checked'
    // A crop a person already checked keeps its label; it still uses up the label it would have had.
    if (blanks.has(at)) return { at, box, label: null, blank: true, kept, agrees: false }
    const from = by.get(source)
    return from?.label ? { at, box, label: from.label, blank: false, kept, agrees: from.label === box.label, source } : null
  }
  const full = []
  // Forward the labels come from `offset` on, backward from the place before it.
  for (let at = 0, source = offset; by.has(at); at++) {
    const step = make(at, source)
    if (!step) break
    full.push(step)
    if (!step.blank) source++
  }
  for (let at = -1, source = offset - 1; by.has(at); at--) {
    const step = make(at, source)
    if (!step) break
    full.unshift(step)
    if (!step.blank) source--
  }
  const here = full.findIndex(step => step.at === 0)
  if (here < 0 || full[here].agrees) return { steps: [], total: 0, writes: [], more: { start: false, end: false } }
  let lo = here, hi = here
  while (lo > 0 && !full[lo - 1].agrees) lo--
  while (hi < full.length - 1 && !full[hi + 1].agrees) hi++
  const start = Math.max(0, Math.min(lo + skip, full.length)), end = Math.max(start, Math.min(hi + 1 - cut, full.length))
  const steps = full.slice(start, end)
  return { steps, total: hi - lo + 1, writes: steps.filter(step => !step.kept), more: { start: start > 0, end: end < full.length } }
}

/** The offset one box on from `offset` in `direction` (-1 or 1), or `offset` at the line's end; never the crop itself. */
export function moveOffset(items, offset, direction) {
  const offsets = items.map(item => item.offset).filter(at => at !== 0).sort((a, b) => a - b)
  const next = direction > 0 ? offsets.find(at => at > offset) : offsets.findLast(at => at < offset)
  return next ?? offset
}

/** The body of the correction that records a shift: one entry for each crop it writes, with the box
 *  redrawn for it (`boxes`, by offset, in page pixels) beside its label. A box that is no character keeps its own. */
export function shiftBody(id, steps, boxes = {}) {
  return { id, line: true, crops: steps.map(({ at, box, label, blank }) => ({ id: box.id, revision: box.revision, image_sha256: box.image_sha256,
    ...(blank ? { issue: 'blank' } : { character: label, ...(boxes[at] ? { box: boxes[at] } : {}) }) })) }
}
