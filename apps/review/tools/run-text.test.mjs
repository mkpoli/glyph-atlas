import { describe, expect, it } from 'bun:test'
import { runText } from '../src/lib/runs.js'

describe('a typed run', () => {
  it('is two to eight characters', () => {
    expect(runText('申候')).toBe('申候')
    expect(runText(' 一二三四五六七八 ')).toBe('一二三四五六七八')
    expect(runText('申')).toBe('')
    expect(runText('一二三四五六七八九')).toBe('')
  })
  it('counts a character with its marks as one', () => {
    expect(runText('が')).toBe('')
    expect(runText('がな')).toBe('がな')
    expect(runText('葛\u{E0100}城')).toBe('葛\u{E0100}城')
  })
  it('is never a name, a code point or words', () => {
    for (const query of ['tomo', 'U+3042', 'U+12', 'あ い', 'あa']) expect(runText(query)).toBe('')
  })
})
