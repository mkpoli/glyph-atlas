import { readFileSync } from 'node:fs'
import { afterAll, beforeEach, describe, expect, it, mock } from 'bun:test'

const request = mock()
mock.module('../src/lib/client.js', () => ({ request }))
const { beside, clearReading, cropLine, opening, pressed, readingEntries, rubyLine, setReading, stretch, textParts, lineRubies, openingTranscribed, transcribedEntries, transcribedRubies, readingsElsewhere, rubiesElsewhere, wordElsewhere } = await import('../src/lib/cropReadings.js')
const { HENTAIGANA, lettersAt, plainKana } = await import('../src/lib/hentaigana.js')
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

describe('the runs an inspector shows', () => {
  const run = { run: ['諸', '器'], at: 1 }, alone = { run: ['器'], at: 0 }
  it('are each as the page writes it, with the notes of how it is read', () => {
    const slot = { values: [{ ...run, reading: ['しょ', 'き'] }, { ...alone, reading: ['うつわ'] }],
      ruby: { values: [{ ...run, ruby: ['𛁈よ', 'き'] }, { ...run, ruby: ['しよ', 'き'] }] }, left: { values: [{ run: ['器', 'に'], at: 0, ruby: ['うつわ'] }] } }
    expect(readingEntries(slot).map(entry => [entry.run.run.join(''), ...['ruby', 'left'].map(side => entry[side].map(value => value.ruby.join(''))), entry.notes.map(value => value.reading.join(''))])).toEqual([
      ['諸器', ['𛁈よき', 'しよき'], [], ['しょき']], ['器に', [], ['うつわ'], []], ['器', [], [], ['うつわ']]])
  })
  it('are none for a crop with none', () => { expect([readingEntries(null), readingEntries({ values: [], ruby: null, left: null })]).toEqual([[], []]) })
})

describe('a ruby on its line', () => {
  const value = { run: ['諸', '器'], at: 1, ruby: ['𛁈よ', 'き'] }, note = { run: ['諸', '器'], at: 1, reading: ['しょ', 'き'] }
  const on = (text, ruby = { status: 'asserted', values: [value] }) => rubyLine({ text, reading: { status: 'asserted', values: [note], ruby } })
  it('stands over the run the line holds, in the letters of the page', () => {
    expect(on('緻密にして諸器に製す')).toEqual({ before: '緻密にして', value, after: 'に製す' })
    expect(on('諸器')).toEqual({ before: '', value, after: '' })
  })
  it('is left off while readers disagree on it, where the line has the run twice or not at all, and for a note alone', () => {
    expect(on('諸器に製す', { status: 'disputed', values: [value, { ...value, ruby: ['しよ', 'き'] }] })).toBeNull()
    for (const text of ['諸器と諸器', '諸噐に製す', '諸', '', null]) expect(on(text)).toBeNull()
    expect(on('諸器', null)).toBeNull()
    for (const reading of [null, undefined]) expect(rubyLine({ text: '諸器', reading })).toBeNull()
    expect(rubyLine(null)).toBeNull()
  })
  it('stands whether or not its reading is disputed', () => {
    expect(rubyLine({ text: '諸器', reading: { status: 'disputed', values: [note, { ...note, reading: ['もろ', 'き'] }], ruby: { status: 'accepted', values: [value] } } })?.value).toBe(value)
  })
})

describe('the rubies a transcription gives a line', () => {
  // 材（ざい）また… 緻密（ちみつ）にして、諸器（しよき）に製（せい）す, counted over letters alone.
  const given = { label: '器', text: '緻密にして、諸器に製す', transcribed_ruby: [[0, 2, 0, 'ちみつ'], [5, 2, 0, ['しよ', 'き']], [5, 2, 1, 'うつわ'], [8, 1, 0, 'せい']] }
  it('are placed on its characters whatever its punctuation', () => {
    expect(transcribedRubies(given).map(one => [one.from, one.to, one.side, one.run.join(''), one.ruby])).toEqual([
      [0, 1, 'right', '緻密', ['ちみつ']], [6, 7, 'right', '諸器', ['しよ', 'き']], [6, 7, 'left', '諸器', ['うつわ']], [9, 9, 'right', '製', ['せい']]])
    expect(transcribedRubies({ text: '諸、器', transcribed_ruby: [[0, 2, 0, 'しよき']] })).toEqual([])
    expect([transcribedRubies({ text: '諸器' }), transcribedRubies(null)]).toEqual([[], []])
  })
  it('stand over the line, a reader\'s ruby first', () => {
    expect(lineRubies(given)).toEqual([{ value: { run: ['緻', '密'], at: -1, ruby: ['ちみつ'] }, transcribed: true }, { text: 'にして、' },
      { value: { run: ['諸', '器'], at: 1, ruby: ['しよ', 'き'] }, transcribed: true }, { text: 'に' }, { value: { run: ['製'], at: -1, ruby: ['せい'] }, transcribed: true }, { text: 'す' }])
    const claimed = { run: ['諸', '器'], at: 1, ruby: ['𛁈よ', 'き'] }
    const line = lineRubies({ ...given, reading: { status: 'asserted', values: [], ruby: { status: 'asserted', values: [claimed] } } })
    expect(line.filter(part => part.value).map(part => [part.value.ruby.join(''), part.transcribed])).toEqual([['ちみつ', true], ['𛁈よき', false], ['せい', true]])
    expect(lineRubies({ text: '諸器' })).toBeNull()
  })
  it('are the crop\'s own entry until a reader writes the run\'s ruby', () => {
    expect(transcribedEntries(given)).toEqual([{ run: { run: ['諸', '器'], at: 1, ruby: ['しよ', 'き'] }, ruby: [{ run: ['諸', '器'], at: 1, ruby: ['しよ', 'き'] }],
      left: [{ run: ['諸', '器'], at: 1, ruby: ['うつわ'] }], notes: [], transcribed: true }])
    expect(readingEntries(null, given).map(entry => entry.transcribed)).toEqual([true])
    const note = { run: ['諸', '器'], at: 1, reading: ['しょ', 'き'] }
    expect(readingEntries({ status: 'asserted', values: [note] }, given).map(entry => [Boolean(entry.transcribed), entry.notes.length])).toEqual([[false, 1], [true, 0]])
    const slot = { status: null, values: [], ruby: { status: 'asserted', values: [{ run: ['諸', '器'], at: 1, ruby: ['𛁈よ', 'き'] }] } }
    expect(readingEntries(slot, given).map(entry => Boolean(entry.transcribed))).toEqual([false])
    expect(transcribedEntries({ ...given, label: '密' })).toMatchObject([{ run: { run: ['緻', '密'], at: 1 } }])
    expect(transcribedEntries({ ...given, label: 'に' })).toEqual([])
  })
  it('are not guessed where the line writes the crop\'s character twice, until its place is known', () => {
    const twice = { label: '器', text: '器を諸器に', transcribed_ruby: [[2, 2, 0, 'しよき']] }
    expect(transcribedEntries(twice)).toEqual([])
    expect(transcribedEntries(twice, 3)).toMatchObject([{ run: { run: ['諸', '器'], at: 1, ruby: ['しよき'] } }])
    expect(lineRubies(twice)[1].value.at).toBe(-1)
  })
  it('are none for a crop its line does not hold', () => {
    const other = { label: '火', text: '山川', transcribed_ruby: [[0, 1, 0, 'やま']] }
    expect([transcribedEntries(other), transcribedEntries(other, 0), openingTranscribed(other)]).toEqual([[], [], null])
    expect(lineRubies(other)[0].value.at).toBe(-1)
  })
  it('wait for neighbours that tell two places of one character apart', () => {
    const twice = { label: '器', text: '器を器に' }
    expect(cropLine(twice, near([[0, '器']]))).toMatchObject({ held: true, sure: false })
    expect(cropLine(twice, near([[-1, 'を'], [0, '器'], [1, 'に']]))).toMatchObject({ at: 2, sure: true })
    expect(cropLine({ label: '器', text: '諸器' })).toMatchObject({ sure: true })
  })
  it('fill the editor where nobody has said anything', () => {
    expect(openingTranscribed(given, 7)).toEqual({ value: { run: ['諸', '器'], at: 1, ruby: ['しよ', 'き'] }, reading: [], ruby: ['しよ', 'き'], left: ['うつわ'], method: null })
    expect([openingTranscribed(given, 4), openingTranscribed({ label: '器', text: '諸器' }, 1)]).toEqual([null, null])
  })
})

describe('what the editor opens with', () => {
  const run = { run: ['諸', '器'], at: 1 }, alone = { run: ['器'], at: 0 }
  const theirs = { ...run, reading: ['しょ', 'き'], by: ['a'], methods: ['ruby'] }, mine = { ...alone, reading: ['うつわ'], by: ['me', 'b'], methods: ['context'] }
  it('is the reading the reader holds, beside another they do not', () => {
    expect(opening({ status: 'disputed', values: [theirs, mine] }, 'me')).toEqual({ value: mine, reading: ['うつわ'], ruby: [], left: [], method: 'context' })
  })
  it('never fills in another reader\'s ruby beside the reader\'s own reading', () => {
    const slot = { status: 'asserted', values: [{ ...theirs, by: ['a', 'me'] }], ruby: { status: 'asserted', values: [{ ...run, ruby: ['𛁈よ', 'き'], by: ['a'] }] } }
    expect(opening(slot, 'me')).toMatchObject({ reading: ['しょ', 'き'], ruby: [] })
    slot.ruby.values.push({ ...run, ruby: ['しよ', 'き'], by: ['me'] }); slot.ruby.status = 'disputed'
    expect(opening(slot, 'me').ruby).toEqual(['しよ', 'き'])
  })
  it('is, for a reader who holds nothing, what nobody disputes', () => {
    const slot = { status: 'asserted', values: [theirs], ruby: { status: 'asserted', values: [{ ...run, ruby: ['𛁈よ', 'き'], by: ['a'] }] } }
    for (const user of ['me', undefined]) expect(opening(slot, user)).toMatchObject({ value: theirs, reading: ['しょ', 'き'], ruby: ['𛁈よ', 'き'], method: 'ruby' })
    expect(opening({ status: 'disputed', values: [theirs, { ...mine, by: ['b'] }] }, 'me')).toEqual({ value: null, reading: [], ruby: [], left: [], method: null })
    expect(opening(null, 'me')).toEqual({ value: null, reading: [], ruby: [], left: [], method: null })
  })
  it('opens with the 左訓 of the same run', () => {
    const left = { status: 'asserted', values: [{ ...run, ruby: ['もろもろのうつわ'], by: ['a'] }] }
    expect(opening({ status: 'asserted', values: [theirs], left }, 'me').left).toEqual(['もろもろのうつわ'])
    expect(opening({ status: 'asserted', values: [{ ...theirs, by: ['me'] }], left }, 'me').left).toEqual([])
    expect(opening({ status: null, values: [], left }, 'me')).toMatchObject({ value: { at: 1 }, left: ['もろもろのうつわ'] })
  })
  it('leaves out a ruby beside another run, and opens with a ruby alone', () => {
    const ruby = { ...alone, ruby: ['き'], by: ['a'] }
    expect(opening({ status: 'asserted', values: [theirs], ruby: { status: 'asserted', values: [ruby] } }, 'me').ruby).toEqual([])
    expect(opening({ status: null, values: [], ruby: { status: 'asserted', values: [ruby] } }, 'me')).toMatchObject({ value: ruby, ruby: ['き'] })
  })
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
      const [point, char, , , kind, , , , jibo, , parent] = line.split('\t')
      if (line.startsWith('#') || kind !== 'hentaigana' || point.includes(' ') || !parent || parent.includes(' ')) continue
      const kana = String.fromCodePoint(parseInt(parent.slice(2), 16))
      table.set(kana, [...table.get(kana) ?? [], { letter: char, jibo }])
    }
    expect([...HENTAIGANA]).toEqual([...table])
  })
})

describe('the letters of a kana in a ruby', () => {
  it('are offered for the kana before the caret, itself first, whichever letter it is written with', () => {
    expect(lettersAt('しよ', 1)).toMatchObject({ from: 0, to: 1, now: 'し' })
    const { letters } = lettersAt('しよ', 1)
    expect(letters[0]).toEqual({ letter: 'し', jibo: null })
    expect(letters).toContainEqual({ letter: '𛁈', jibo: '志' })
    expect(lettersAt('𛁈よ', 2)).toMatchObject({ from: 0, to: 2, now: '𛁈' })
  })
  it('carry the voicing of the kana', () => {
    const found = lettersAt('じ', 1)
    expect(found.letters[0].letter).toBe('じ')
    expect(found.letters).toContainEqual({ letter: '𛁈\u3099', jibo: '志' })
    expect(lettersAt('𛁈\u3099', 3)).toMatchObject({ from: 0, to: 3, now: '𛁈\u3099' })
  })
  it('are none at the start, after a kanji or for a kana with no hentaigana', () => {
    expect([lettersAt('し', 0), lettersAt('器', 1), lettersAt('ー', 1)]).toEqual([null, null, null])
  })
})

describe('the reading client', () => {
  const value = { run: ['諸', '器'], at: 1, reading: ['しょ', 'き'], ruby: ['𛁈よ', 'き'], left: ['うつわ'], method: 'ruby' }
  it('names the other crops of the run at the version they are shown at', async () => {
    const item = crop(), slot = { status: 'asserted' }
    request.mockResolvedValue({ reading: slot, word: null })
    expect(await setReading(item, { ...value, crops: new Map([[0, { id: 'other', crop_version: 'other@shown' }]]) })).toEqual({ ...item, reading: slot, word: null })
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
    await clearReading(item, { word: true })
    expect(request.mock.calls.at(-1)[1]).toMatchObject({ run: null, word: null })
  })
  it('names a word only when it is given one, null to take it back', async () => {
    request.mockResolvedValue({ reading: null, word: null })
    for (const word of [undefined, null, { id: 'ja/のみ/副助詞', form: 'certain', method: 'ruby' }]) await setReading(crop(), { ...value, word })
    expect(request.mock.calls.map(([, body]) => 'word' in body ? body.word : 'none')).toEqual(['none', null, { id: 'ja/のみ/副助詞', form: 'certain', method: 'ruby' }])
  })
})

describe('readings the book gives a word elsewhere', () => {
  // 「この諸器、また器」: 諸器 is read on three other lines, 器 alone two ways.
  const given = crop({ text: 'この諸器、また器', ruby_elsewhere: [[2, 2, ['しよ', 'き'], 3, 28], [3, 1, 'うつは', 1, 29], [3, 1, 'き', 1, 29], [3, 3, 'きまた', 1, 30]] })
  it('places each on the line, and leaves out one the punctuation parts', () => {
    expect(rubiesElsewhere(given)).toEqual([{ from: 2, to: 3, run: ['諸', '器'], reading: ['しよ', 'き'], lines: 3, page: 28 },
      { from: 3, to: 3, run: ['器'], reading: ['うつは'], lines: 1, page: 29 }, { from: 3, to: 3, run: ['器'], reading: ['き'], lines: 1, page: 29 }])
    expect(rubiesElsewhere(crop())).toEqual([])
    expect(rubiesElsewhere({ ...given, ruby_elsewhere: [[3, 0, 'き', 1, 29]] })).toEqual([])
  })
  it('gives a run its readings, and a place the longest word around it', () => {
    expect(readingsElsewhere(given, 2, 3).map(one => one.reading)).toEqual([['しよ', 'き']])
    expect(readingsElsewhere(given, 3, 3).map(one => one.reading)).toEqual([['うつは'], ['き']])
    expect(readingsElsewhere(given, 2, 2)).toEqual([])
    expect(wordElsewhere(given, 3)).toEqual({ from: 2, to: 3 })
    expect(wordElsewhere(given, 2)).toEqual({ from: 2, to: 3 })
    // A character read alone elsewhere is no word to open on.
    expect(wordElsewhere({ ...given, ruby_elsewhere: [[3, 1, 'き', 1, 29]] }, 3)).toBeNull()
    expect(wordElsewhere(given, 0)).toBeNull()
  })
})
