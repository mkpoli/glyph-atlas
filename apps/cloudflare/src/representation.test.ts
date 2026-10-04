import { expect, test } from 'bun:test'
import { anchoredForm, formProblem, isSequence, representationId, typed, type Representation } from './representation'

test('a character or a description is a form a reviewer may type', () => {
  for (const value of ['𮟃', '還', 'が', '葛\u{E0100}', '⿺辶𦊷', '⿰氵⿱冖月', '⿲木木木', '⿱⿰口口⿰口口', '〾⿰木？', '⿰木', '⿰⺡骨'])
    expect(formProblem(value)).toBeNull()
  expect(isSequence('⿺辶𦊷')).toBe(true)
  expect(isSequence('𮟃')).toBe(false)
})

test('anything else names what is wrong with it', () => {
  expect(['', ' 還', '還還', '\u3099', 'x'.repeat(65), '⿰木\u200b', '⿰木 木', '🇯🇵', '👍🏽', '\u1100\u1161'].map(formProblem)).toEqual(Array(10).fill('character'))
  expect(['⿰木', '⿰', '⿲木木'].map(formProblem)).toEqual(['missing', 'missing', 'missing'])
  expect(['⿰木木木', '⿰木木︀木'].map(formProblem)).toEqual(['extra', 'extra'])
  expect(['⿰木a', '⿰木あ', '⿰木ー'].map(formProblem)).toEqual(['component', 'component', 'component'])
  // A katakana letter stands for a component of its shape, as manuscripts write 疑 with コ.
  expect(['⿰⿱匕失⿱コ疋', '⿰木ア'].map(formProblem)).toEqual([null, null])
})

test('what is typed takes the scheme it is written in', () => {
  expect(typed('𮟃')).toEqual({ scheme: 'unicode', value: '𮟃', namespace: null, version: null })
  expect(typed('⿺辶𦊷')).toEqual({ scheme: 'ids', value: '⿺辶𦊷', namespace: null, version: null })
  expect(typed('葛\u{E0100}')).toEqual({ scheme: 'ivs', value: '葛\u{E0100}', namespace: null, version: null })
  expect(typed('\uE000')).toBe('private')
  expect(typed('⿰木')).toBe('missing')
})

test('ids are what the review service derives for the same names', async () => {
  // tests/test_representation.py holds the same values.
  const vectors: [string, string, string][] = [['𮟃', 'rp:b78a8585df2e755ac741ca9c16c933f4', 'fm:9a7e168e47aef52e2958cff4ba7cf858'],
    ['⿺辶𦊷', 'rp:4fd4eff3084f8bbbe5489e119eea2862', 'fm:9c8fb02868fdcbc3357a0b63ab17f5f4'],
    ['葛\u{E0100}', 'rp:e3640385ca612cd97bec3b1e527fd705', 'fm:8b7650bbf9099669cfa0f0132a3bd18a']]
  for (const [value, rep, form] of vectors) {
    const id = await representationId(typed(value) as Representation)
    expect([id, await anchoredForm(id)]).toEqual([rep, form])
  }
  expect(await representationId({ scheme: 'pua', value: '\uE000', namespace: 'example', version: '1' })).toBe('rp:24234b052e1ad13df2cca72c95de5246')
})
