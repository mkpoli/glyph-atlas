import { expect, test } from 'bun:test'
import { formProblem, isSequence } from './writtenForm'

test('a character or a description is a written form', () => {
  for (const value of ['𮟃', '還', 'が', '葛\u{E0100}', '⿺辶𦊷', '⿰氵⿱冖月', '⿲木木木', '⿱⿰口口⿰口口', '〾⿰木？', '⿰木', '⿰⺡骨'])
    expect(formProblem(value)).toBeNull()
  expect(isSequence('⿺辶𦊷')).toBe(true)
  expect(isSequence('𮟃')).toBe(false)
})

test('anything else names what is wrong with it', () => {
  expect(['', ' 還', '還還', '\u3099', 'x'.repeat(65), '⿰木\u200b', '⿰木 木', '🇯🇵', '👍🏽', '\u1100\u1161'].map(formProblem)).toEqual(Array(10).fill('character'))
  expect(['⿰木', '⿰', '⿲木木'].map(formProblem)).toEqual(['missing', 'missing', 'missing'])
  expect(['⿰木木木', '⿰木木︀木'].map(formProblem)).toEqual(['extra', 'extra'])
  expect(['⿰木a', '⿰木ア'].map(formProblem)).toEqual(['component', 'component'])
})
