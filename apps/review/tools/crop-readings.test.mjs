import { readFileSync } from 'node:fs'
import { afterAll, beforeEach, describe, expect, it, mock } from 'bun:test'

const request = mock()
mock.module('../src/lib/client.js', () => ({ request }))
const { beside, clearReading, cropLine, pressed, setReading, stretch, textParts } = await import('../src/lib/cropReadings.js')
const { HENTAIGANA, plainKana } = await import('../src/lib/hentaigana.js')
afterAll(() => mock.restore())
beforeEach(() => request.mockReset())

const near = labels => labels.map(([offset, label]) => ({ id: label + offset, offset, label }))
const crop = (value = {}) => ({ id: 'c' + crypto.randomUUID(), crop_version: 'v', label: '器', text: '緻密にして諸器に製す', ...value })

describe('a crop on its line', () => {
  const at = (item, neighbours) => { const line = cropLine(item, neighbours); return { ...line, crops: beside(line.parts, line.at, neighbours) } }
  it('stands at its character, with the crops beside it that the text agrees with', () => {
    const line = at(crop({ text: '緻密にして、諸器に製す' }), near([[-2, 'て'], [-1, '諸'], [0, '器']]))
    expect([line.at, line.parts[line.at], line.also]).toEqual([7, '器', []])
    expect([...line.crops].map(([place, one]) => [place, one.label])).toEqual([[6, '諸'], [4, 'て']])
  })
  it('is the place whose neighbours agree most, where the character is written twice', () => {
    const line = at(crop({ text: '器を作り諸器に製す' }), near([[-1, '諸'], [0, '器'], [1, 'に']]))
    expect([line.at, line.also, [...line.crops.keys()]]).toEqual([5, [0], [6, 4]])
    expect(cropLine(crop({ label: '噐', text: '諸器' }), []).parts).toEqual(['噐'])
  })
  it('stops a side at a crop the text does not give', () => {
    expect([...at(crop(), near([[-2, 'て'], [-1, '緒'], [0, '器'], [1, 'に']])).crops.keys()]).toEqual([7])
  })
  it('stops a side past a crop that is not published, so a repeated character is never another\'s', () => {
    expect([...at(crop({ text: '器ここに' }), near([[0, '器'], [2, 'こ']])).crops.keys()]).toEqual([])
  })
  it('is found by a compatibility ideograph its line composes', () => {
    expect(at(crop({ label: '\uFA19', text: '明神社' }), near([[-1, '明'], [0, '\uFA19']]))).toMatchObject({ at: 1, parts: ['明', '\u795E', '社'] })
    expect(cropLine(crop({ label: '\uFA19', text: null }), []).parts).toEqual(['\u795E'])
  })
  it('without a line that holds it, stands among its neighbours, or alone', () => {
    const line = at(crop({ text: null }), near([[-1, '諸'], [0, '器'], [1, 'に']]))
    expect([line.parts, line.at, [...line.crops.keys()]]).toEqual([['諸', '器', 'に'], 1, [2, 0]])
    expect(cropLine(crop({ text: null }), [])).toEqual({ parts: ['器'], at: 0, also: [] })
  })
  it('keeps a variation selector and a mark with their character', () => {
    expect(textParts('突󠄃か\u309a')).toEqual(['突󠄃', 'か゚'])
  })
})

it('a run reaches the punctuation either side of its crop', () => {
  expect(stretch(textParts('あり、諸器に製す'), 4)).toEqual({ from: 3, to: 7 })
  expect(stretch(textParts('器'), 0)).toEqual({ from: 0, to: 0 })
  // A repetition mark, a damage mark and a drawn form are read with the characters around them.
  expect(stretch(textParts('。云々ゝ〱〓\u{F0000}、'), 1)).toEqual({ from: 1, to: 6 })
})

it('a press grows the run to a character outside it, and leaves one inside it out', () => {
  expect(pressed({ from: 5, to: 5 }, 5, 4)).toEqual({ from: 4, to: 5 })
  expect(pressed({ from: 4, to: 5 }, 5, 7)).toEqual({ from: 4, to: 7 })
  expect(pressed({ from: 3, to: 7 }, 5, 6)).toEqual({ from: 3, to: 5 })
  expect(pressed({ from: 3, to: 5 }, 5, 3)).toEqual({ from: 4, to: 5 })
  expect(pressed({ from: 3, to: 5 }, 5, 5)).toEqual({ from: 3, to: 5 })
  expect(pressed({ from: 20, to: 20 }, 20, 0)).toEqual({ from: 9, to: 20 })
})

describe('hentaigana', () => {
  it('are read as the kana they are letters of', () => {
    expect(plainKana('𛁈よき')).toBe('しよき')
    expect(plainKana('𛁈゙')).toBe('じ')
    expect(plainKana('シヨ')).toBe('シヨ')
  })
  it('are the ones of the character table', () => {
    const table = new Map()
    for (const line of readFileSync(new URL('../../../data/vocab/characters.tsv', import.meta.url), 'utf8').split('\n')) {
      const [point, char, , , kind, , , , , , parent] = line.split('\t')
      if (line.startsWith('#') || kind !== 'hentaigana' || point.includes(' ') || !parent || parent.includes(' ')) continue
      const kana = String.fromCodePoint(parseInt(parent.slice(2), 16))
      table.set(kana, (table.get(kana) ?? '') + char)
    }
    expect([...HENTAIGANA]).toEqual([...table])
  })
})

describe('the reading client', () => {
  const value = { run: ['諸', '器'], at: 1, reading: ['しょ', 'き'], ruby: ['𛁈よ', 'き'], method: 'ruby' }
  it('names the other crops of the run at the version they are shown at', async () => {
    const item = crop(), slot = { status: 'asserted' }
    request.mockResolvedValue({ reading: slot })
    expect(await setReading(item, { ...value, crops: new Map([[0, { id: 'other', crop_version: 'other@shown' }]]) })).toEqual({ ...item, reading: slot })
    expect(request.mock.calls[0]).toEqual([`/atlas/characters/${item.id}/reading`,
      { id: expect.any(String), crop_version: 'v', ...value, others: [{ id: 'other', crop_version: 'other@shown', at: 0 }] }])
  })
  it('sends a lost save again as itself, an answered or refused one anew', async () => {
    const item = crop()
    request.mockRejectedValueOnce(new TypeError('network')).mockRejectedValueOnce(Object.assign(new Error('stale'), { status: 409 })).mockResolvedValue({ reading: null })
    await expect(setReading(item, value)).rejects.toThrow('network')
    await expect(setReading(item, value)).rejects.toThrow('stale')
    await setReading(item, value); await setReading(item, value)
    const ids = request.mock.calls.map(([, body]) => body.id)
    expect(ids[0]).toBe(ids[1])
    expect(new Set(ids).size).toBe(3)
    await clearReading(item)
    expect(request.mock.calls.at(-1)[1]).toEqual({ id: expect.any(String), crop_version: 'v', run: null })
  })
})
