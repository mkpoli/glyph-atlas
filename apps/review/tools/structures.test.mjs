import { describe, expect, it, mock } from 'bun:test'

const calls = []
mock.module('../src/lib/layers.js', () => ({
  structures: async (chars, of) => {
    calls.push({ chars, of })
    const items = [...chars].map((c, i) => ({
      id: `st:${c}`,
      label: c,
      chars: [c],
      ...(i === 0 ? { claimed: { [c]: ['u1'] } } : {}),
      folded: i === 1 ? [{ id: `part:${c}`, label: c, by: ['u2'], chars: [c] }] : [],
    }))
    return { items, filed: { [items[0].chars[0]]: [{ id: 'st:alt', label: 'alt', by: ['u3'] }] } }
  },
}))

const { NAMES, standing } = await import('../src/lib/structures.js')

describe('reading structures beyond NAMES', () => {
  it('chunks composable names by NAMES and merges every answer into known', async () => {
    calls.length = 0
    const han = Array.from({ length: NAMES + 5 }, (_, i) => String.fromCodePoint(0x4e00 + i))
    const initial = new Map([
      ['あ', { id: 'kept', label: 'kept', by: null, others: [], part: null }],
      [han[0], { id: 'stale', label: 'stale', by: null, others: [], part: null }],
    ])

    const result = await standing(['あ', ...han, han[0]], initial, '瓜')

    expect(calls).toHaveLength(2)
    expect([...calls[0].chars]).toHaveLength(NAMES)
    expect([...calls[1].chars]).toHaveLength(5)
    expect(calls.every(call => call.of === '瓜')).toBe(true)
    expect(result.get('あ')).toEqual({ id: 'kept', label: 'kept', by: null, others: [], part: null })
    expect(result.get(han[0])).toEqual({
      id: `st:${han[0]}`,
      label: han[0],
      by: ['u1'],
      others: [{ id: 'st:alt', label: 'alt', by: ['u3'] }],
      part: null,
    })
    expect(result.get(han[NAMES + 4])?.id).toBe(`st:${han[NAMES + 4]}`)
    expect(result.get(han[1])?.part).toEqual({ id: `part:${han[1]}`, label: han[1], by: ['u2'], chars: [han[1]] })
  })

  it('returns known unchanged when no name is composable', async () => {
    calls.length = 0
    const initial = new Map([['a', { id: '1', label: 'a', by: null, others: [], part: null }]])
    const result = await standing(['あ', 'カ'], initial)
    expect(calls).toHaveLength(0)
    expect(result).toBe(initial)
  })
})
