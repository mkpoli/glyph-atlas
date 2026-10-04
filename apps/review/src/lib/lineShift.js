// A line whose labels sit one box off: the crop shows the character that its neighbour's label names.
// The strip's `items` are the crop and its neighbours with their `offset` from it (0 is the crop) and
// their label. Moving the run by `offset` gives each crop, from the first on, the label of the box
// `offset` places away, until a crop already holds the label it would get. A box marked as no character
// takes no label and the labels carry on past it, so the boxes after it are one place further along.
// `skip` and `cut` trim the run's start and end.

/** The boxes of a shift by `offset`, in reading order: `{ at, box, label, blank, kept }`. */
export function planShift(items, { offset, skip = 0, cut = 0, blanks = new Set() }) {
  const by = new Map(items.map(item => [item.offset, item]))
  const run = []
  let source = offset
  for (let at = 0; by.has(at); at++) {
    const box = by.get(at)
    // A crop a person already checked keeps its label; it still uses up the label it would have had.
    const kept = box.state === 'checked'
    if (blanks.has(at)) { run.push({ at, box, label: null, blank: true, kept }); continue }
    const from = by.get(source)
    if (!from?.label || from.label === box.label) break
    run.push({ at, box, label: from.label, blank: false, kept })
    source++
  }
  const steps = run.slice(skip, Math.max(skip, run.length - cut))
  return { steps, total: run.length, writes: steps.filter(step => !step.kept) }
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
