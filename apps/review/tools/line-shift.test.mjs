import { describe, expect, it } from 'bun:test'
import { moveOffset, planShift, shiftBody } from '../src/lib/lineShift.js'

const line = (labels, first = -2, states = {}) => labels.map((label, i) => ({ id: `c${i}`, offset: first + i, label, revision: 1, image_sha256: 'h', state: states[i] ?? 'pending' }))
const read = plan => plan.steps.map(step => [step.at, step.label ?? '_'])

describe('a shift of a line', () => {
  // Boxes -2..4 hold あきもぬねのは; box 0 is も, and き (box -1) is where its label sits.
  const items = line([...'あきもぬねのは'])
  it('runs both ways from the crop, to the line\'s ends when no label agrees', () => {
    expect(read(planShift(items, { offset: -1 }))).toEqual([[-1, 'あ'], [0, 'き'], [1, 'も'], [2, 'ぬ'], [3, 'ね'], [4, 'の']])
    expect(read(planShift(items, { offset: 1 }))).toEqual([[-2, 'き'], [-1, 'も'], [0, 'ぬ'], [1, 'ね'], [2, 'の'], [3, 'は']])
  })
  it('stops on each side where a crop already holds the label it would get', () => {
    const agree = line([...'あきもぬのの'], -3)
    expect(read(planShift(agree, { offset: -1 }))).toEqual([[-2, 'あ'], [-1, 'き'], [0, 'も'], [1, 'ぬ']])
    // box -2 already holds the label it would get (あ), so the run starts after it
    const back = line([...'いあきもぬね'], -3).map(item => item.offset === -3 ? { ...item, label: 'あ' } : item)
    expect(read(planShift(back, { offset: -1 }))).toEqual([[-1, 'あ'], [0, 'き'], [1, 'も'], [2, 'ぬ']])
  })
  it('is empty when the box named holds the same label', () => {
    expect(planShift(line([...'いああ']), { offset: -1 }).steps).toEqual([])
  })
  it('trims either end inward, and moves either end outward past where the labels agreed', () => {
    expect(read(planShift(items, { offset: -1, skip: 2, cut: 2 }))).toEqual([[1, 'も'], [2, 'ぬ']])
    expect(planShift(items, { offset: -1, skip: 9 }).steps).toEqual([])
    const capped = line([...'あきもぬのの'], -3)
    const wide = planShift(capped, { offset: -1, cut: -1 })
    expect(read(wide).at(-1)).toEqual([2, 'の'])
    expect(planShift(capped, { offset: -1 }).more.end).toBe(true)
    expect(planShift(items, { offset: -1 }).more).toEqual({ start: false, end: false })
  })
  it('lets the labels carry on past a box that is no character, at either end', () => {
    expect(read(planShift(items, { offset: -1, blanks: new Set([1]) }))).toEqual([[-1, 'あ'], [0, 'き'], [1, '_'], [2, 'も'], [3, 'ぬ'], [4, 'ね']])
    expect(read(planShift(items, { offset: -1, blanks: new Set([-1]) }))).toEqual([[-1, '_'], [0, 'き'], [1, 'も'], [2, 'ぬ'], [3, 'ね'], [4, 'の']])
  })
  it('leaves a crop a person checked as it is, and does not write it', () => {
    const plan = planShift(line([...'あきもぬね'], -2, { 4: 'checked' }), { offset: -1 })
    expect(plan.steps.map(step => [step.at, step.kept])).toEqual([[-1, false], [0, false], [1, false], [2, true]])
    expect(plan.writes.map(step => step.at)).toEqual([-1, 0, 1])
  })
  it('names each crop it writes with the revision and pixels it saw', () => {
    const plan = planShift(items, { offset: -1, skip: 1, cut: 3, blanks: new Set([1]) })
    expect(shiftBody('s', plan.writes)).toEqual({ id: 's', line: true, crops: [
      { id: 'c2', revision: 1, image_sha256: 'h', character: 'き' }, { id: 'c3', revision: 1, image_sha256: 'h', issue: 'blank' }] })
  })
  it('carries the box redrawn for a crop beside its label, and none for a box that is no character', () => {
    const plan = planShift(items, { offset: -1, skip: 1, cut: 3, blanks: new Set([1]) })
    const box = { x: 1, y: 2, w: 3, h: 4 }
    expect(shiftBody('s', plan.writes, { 0: box, 1: box }).crops).toEqual([
      { id: 'c2', revision: 1, image_sha256: 'h', character: 'き', box }, { id: 'c3', revision: 1, image_sha256: 'h', issue: 'blank' }])
  })
  it('moves the chosen box one place, past the crop itself and not beyond the line', () => {
    expect(moveOffset(items, -1, 1)).toBe(1)
    expect(moveOffset(items, 1, -1)).toBe(-1)
    expect(moveOffset(items, -2, -1)).toBe(-2)
    expect(moveOffset(items, 4, 1)).toBe(4)
  })
})
