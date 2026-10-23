import { describe, expect, it } from 'bun:test'
import { undoOutcome } from '../src/lib/undoOutcome.js'

describe('an undo answer', () => {
  it('counts the crops left because a publication changed them since apart from those undone', () => {
    expect(undoOutcome({ results: [{ id: 1, target_id: 'a' }, { id: null, target_id: 'b', refused: 'This crop was published again since this review.' }] }))
      .toEqual({ refused: 1, undone: 1 })
    expect(undoOutcome({ results: [], duplicate: true })).toEqual({ refused: 0, undone: 0 })
    expect(undoOutcome(null)).toEqual({ refused: 0, undone: 0 })
  })
})
