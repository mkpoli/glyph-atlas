import { describe, expect, it } from 'bun:test'
import { mergeFigures } from '../src/lib/figures.js'

// 辶 U+8FB6: three Moji_Joho figures (the MJ figure list) and the selectors each face's figure file
// lists for it, counted from U+E0100 (Adobe-Japan1 holds the first two).
const registered = [['\u{E0102}', 'MJ030302'], ['\u{E0103}', 'MJ025753'], ['\u{E0104}', 'MJ058837']].map(([selector, mj]) => ({ char: '辶' + selector, mj, default: mj === 'MJ025753' }))
const sans = { '8FB6': [0, 1] }, serif = { '8FB6': [0, 1, 2, 3, 4] }

describe('the figures of a part', () => {
  it('lists every sequence a font draws once, in selector order, with its MJ figure', () => {
    expect(mergeFigures('辶', registered, [sans, serif])).toEqual([
      { char: '辶\u{E0100}', mj: null }, { char: '辶\u{E0101}', mj: null },
      { char: '辶\u{E0102}', mj: 'MJ030302' }, { char: '辶\u{E0103}', mj: 'MJ025753' }, { char: '辶\u{E0104}', mj: 'MJ058837' }])
  })

  it('lists the sequences of a character with no Moji_Joho figure', () => {
    // 禺 U+79BA has the two Adobe-Japan1 sequences alone.
    expect(mergeFigures('禺', [], [{ '79BA': [0, 1] }, { '79BA': [0, 1] }]).map(figure => figure.char)).toEqual(['禺\u{E0100}', '禺\u{E0101}'])
  })

  it('lists none for a character no file names', () => {
    expect(mergeFigures('一', [], [{}, {}])).toEqual([])
  })
})
