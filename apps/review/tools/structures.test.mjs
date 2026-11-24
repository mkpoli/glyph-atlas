import { describe, expect, it, mock } from 'bun:test'

const calls = []
const shapes = { 臭: [{ text: '⿱自犬', regions: 'GHTKPV' }, { text: '⿱自大', regions: 'J', chars: ['𦤀'] }] }
const asked = []
let answer = null
const FAIL = '鬱'
mock.module('../src/lib/layers.js', () => ({
  ids: async char => { asked.push(char); return { shapes: shapes[char] ?? [] } },
  structures: async (chars, of) => {
    if (answer) return { items: answer }
    calls.push({ chars, of })
    if (chars.includes(FAIL)) throw new Error('429')
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

const { cardStructures, NAMES, proposed, standing } = await import('../src/lib/structures.js')

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

  it('keeps what was known of a chunk whose read failed, and takes the others', async () => {
    calls.length = 0
    const han = Array.from({ length: NAMES }, (_, i) => String.fromCodePoint(0x4e00 + i))
    const initial = new Map([[FAIL, { id: 'old', label: 'old', by: null, others: [], part: null }]])
    const result = await standing([...han, FAIL], initial)
    expect(calls).toHaveLength(2)
    expect(result.get(FAIL)?.id).toBe('old')
    expect(result.get(han[0])?.id).toBe(`st:${han[0]}`)
  })

  it('fails when every chunk fails', async () => {
    await expect(standing([FAIL])).rejects.toThrow('429')
  })

  it('asks for a small seal character beside its Han equivalent', async () => {
    calls.length = 0
    const result = await standing(['臭', '\u{3EC9F}', 'あ'])
    expect(calls.map(call => call.chars)).toEqual(['臭\u{3EC9F}'])
    expect(result.has('\u{3EC9F}')).toBe(true)
  })

  it('returns known unchanged when no name is composable', async () => {
    calls.length = 0
    const initial = new Map([['a', { id: '1', label: 'a', by: null, others: [], part: null }]])
    const result = await standing(['あ', 'カ'], initial)
    expect(calls).toHaveLength(0)
    expect(result).toBe(initial)
  })
})

describe("the 構形 of a character's card", () => {
  const read = async (char, items) => { asked.length = 0; answer = items; try { return await cardStructures(char, '') } finally { answer = null } }

  it('leaves a character drawn as the bare character out of the group the two share', async () => {
    const items = [{ id: 'st:1', label: '⿱自犬', chars: ['臭', '𦤀', '\u{3EC9F}'] }, { id: 'st:2', label: '⿱自死', chars: ['臰'] }]
    const found = await read('臭', items)
    expect([found.items.map(group => group.chars.join('')), found.twins]).toEqual([['臭\u{3EC9F}', '臰'], ['𦤀']])
    expect(asked).toEqual(['臭'])
  })

  it('keeps it where a reader filed it', async () => {
    const items = [{ id: 'st:1', label: '⿱自犬', chars: ['臭'] }, { id: 'st:3', label: '⿱自大', chars: ['𦤀'], claimed: { 𦤀: ['u1'] } }]
    const found = await read('臭', items)
    expect([found.items.map(group => group.chars.join('')), found.twins]).toEqual([['臭', '𦤀'], []])
    expect(asked).toEqual([])
  })

  it('keeps it where a reader filed it under a 構形 folded into this one', async () => {
    const items = [{ id: 'st:1', label: '⿱自犬', chars: ['臭', '𦤀'], claimed: { 𦤀: ['u1'] }, folded: [{ id: 'st:3', label: '⿱自大', by: ['u1'], chars: ['𦤀'] }] }]
    const found = await read('臭', items)
    expect([found.items[0].chars, found.twins]).toEqual([['臭', '𦤀'], ['𦤀']])
  })

  it('marks a seal character nobody filed as proposed', () => {
    expect([proposed('\u{3EC9F}', undefined), proposed('\u{3EC9F}', ['u1']), proposed('臭', undefined)]).toEqual([true, false, false])
  })
})
