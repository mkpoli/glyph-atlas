import { describe, expect, it } from 'bun:test'
import { moveOffset, planShift, shiftBody } from '../src/lib/lineShift.js'

const line = (labels, first = -2, states = {}) => labels.map((label, i) => ({ id: `c${i}`, offset: first + i, label, revision: 1, image_sha256: 'h', state: states[i] ?? 'pending' }))
const read = plan => plan.steps.map(step => [step.at, step.label ?? '_'])

describe('a shift of a line', () => {
  // Boxes -2..4 hold: crop 11 is -1 (shows 人, labelled き), this crop 0 shows き (labelled も), box 1 shows も (labelled ぬ).
  const items = line([...'あきもぬねのは'])
  it('gives each crop the label of the box an offset away, to the end of the strip', () => {
    expect(read(planShift(items, { offset: -1 }))).toEqual([[0, 'き'], [1, 'も'], [2, 'ぬ'], [3, 'ね'], [4, 'の']])
    expect(read(planShift(items, { offset: 1 }))).toEqual([[0, 'ぬ'], [1, 'ね'], [2, 'の'], [3, 'は']])
  })
  it('stops where a crop already holds the label it would get', () => {
    const agree = line([...'あきもぬのの'])
    expect(read(planShift(agree, { offset: -1 }))).toEqual([[0, 'き'], [1, 'も'], [2, 'ぬ']])
  })
  it('is empty when the box named holds the same label', () => {
    expect(planShift(line([...'いああ']), { offset: -1 }).steps).toEqual([])
  })
  it('trims either end', () => {
    expect(read(planShift(items, { offset: -1, skip: 1, cut: 2 }))).toEqual([[1, 'も'], [2, 'ぬ']])
    expect(planShift(items, { offset: -1, skip: 9 }).steps).toEqual([])
  })
  it('lets the labels carry on past a box that is no character', () => {
    expect(read(planShift(items, { offset: -1, blanks: new Set([1]) }))).toEqual([[0, 'き'], [1, '_'], [2, 'も'], [3, 'ぬ'], [4, 'ね']])
  })
  it('leaves a crop a person checked as it is, and does not write it', () => {
    const plan = planShift(line([...'あきもぬね'], -2, { 4: 'checked' }), { offset: -1 })
    expect(plan.steps.map(step => [step.at, step.kept])).toEqual([[0, false], [1, false], [2, true]])
    expect(plan.writes.map(step => step.at)).toEqual([0, 1])
  })
  it('names each crop it writes with the revision and pixels it saw', () => {
    const plan = planShift(items, { offset: -1, cut: 3, blanks: new Set([1]) })
    expect(shiftBody('s', plan.writes)).toEqual({ id: 's', line: true, crops: [
      { id: 'c2', revision: 1, image_sha256: 'h', character: 'き' }, { id: 'c3', revision: 1, image_sha256: 'h', issue: 'blank' }] })
  })
  it('moves the chosen box one place, past the crop itself and not beyond the line', () => {
    expect(moveOffset(items, -1, 1)).toBe(1)
    expect(moveOffset(items, 1, -1)).toBe(-1)
    expect(moveOffset(items, -2, -1)).toBe(-2)
    expect(moveOffset(items, 4, 1)).toBe(4)
  })
})
