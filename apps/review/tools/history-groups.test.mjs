import { describe, expect, it } from 'bun:test'
import { groupRuns } from '../src/lib/historyGroups.js'

const row = (id, user, minute, batch = null) => ({ id, batch, items: [{ id, at: `2026-10-02T23:${String(minute).padStart(2, '0')}:00.000Z`, reviewer: { user, name: user } }] })

describe('history runs', () => {
  it('fold rows by one reviewer within ten minutes of each other, newest first', () => {
    const groups = groupRuns([row('a', 'u1', 50), row('b', 'u1', 45), row('c', 'u1', 36), row('d', 'u1', 20), row('e', 'u2', 19), row('f', 'u1', 18)])
    expect(groups.map(group => group.items.map(item => item.id))).toEqual([['a', 'b', 'c'], ['d'], ['e'], ['f']])
  })
})
