import { describe, expect, it } from 'bun:test'
import { notoDraws, sortForms } from '../src/lib/formOffers.js'

// 邦's Moji_Joho figures as the MJ figure list gives them, with their IVD descriptions; MJ026242 is
// the default.
const bangFigures = [['\u{E0103}', 'MJ026242', true, '⿰丰阝'], ['\u{E0104}', 'MJ026246', false, '⿰龵阝'], ['\u{E0105}', 'MJ026244', false, '⿰丰阝']]
  .map(([selector, mj, isDefault, ids]) => ({ char: '邦' + selector, mj, default: isDefault, ids }))
// 臭's: MJ021299 drawn ⿱自大, the default, and MJ030291 drawn ⿱自犬.
const chouFigures = [{ char: '臭\u{E0102}', mj: 'MJ021299', default: true, ids: '⿱自大' },
  { char: '臭\u{E0103}', mj: 'MJ030291', default: false, ids: '⿱自犬' }]
// Shapes as /layers/ids gives them.
const bangShapes = [{ text: '⿰丰阝', regions: 'GHTJKV' }, { text: '⿰⿻㇒𰀁阝', regions: 'P' }]
// 𦤀 encodes 臭's J shape again.
const chouShapes = [{ text: '⿱自犬', regions: 'GHTKPV' }, { text: '⿱自大', regions: 'J', chars: ['𦤀'] }]

describe('form offers', () => {
  it("offers a figure in place of the regional shape that is its description", () => {
    const sorted = sortForms('臭', chouFigures, chouShapes)
    expect(sorted.figures.map(figure => figure.mj)).toEqual(['MJ030291'])
    expect(sorted.alike.shapes.map(shape => shape.text)).toContain('⿱自犬')
  })

  it('sets apart 臭\'s J shape and 𦤀, which encodes it again: both draw as 臭', () => {
    const sorted = sortForms('臭', chouFigures, chouShapes)
    expect(sorted.shapes).toEqual([])
    expect(sorted.alike.shapes.map(shape => shape.text)).toEqual(['⿱自犬', '⿱自大'])
    expect(sorted.alike.chars).toEqual(['𦤀'])
  })

  it('keeps a shape only a set-aside figure describes', () => {
    const shapes = [{ text: '⿱自大', regions: 'TV' }]
    expect(sortForms('臭', chouFigures, shapes).shapes).toEqual(shapes)
  })

  it('offers the character a shape is in place of a shape outside J', () => {
    // 稽's K shape ⿰禾⿱尤㫖 is 𥡴, which the bar offers; 𥡴 is not drawn as 稽, so it stays.
    const sorted = sortForms('稽', [], [{ text: '⿰禾⿱尤旨', regions: 'GHTJPV' }, { text: '⿰禾⿱尤㫖', regions: 'K', chars: ['𥡴'] }])
    expect(sorted.shapes).toEqual([])
    expect(sorted.alike.chars).toEqual([])
  })

  it('offers a figure in place of its description for a character another font draws', () => {
    // 𠦄 U+20984: BabelStone gives ⿱十⿰𠂇十 for G and P and ⿱十卄 for V.
    const figures = [{ char: '𠦄\u{E0101}', mj: 'MJ031728', default: false, ids: '⿱十⿰𠂇十' },
      { char: '𠦄\u{E0102}', mj: 'MJ031729', default: true, ids: '⿱十⿰十十' }]
    const sorted = sortForms('𠦄', figures, [{ text: '⿱十⿰𠂇十', regions: 'GP' }, { text: '⿱十卄', regions: 'V' }])
    expect(sorted.figures).toEqual(figures)
    expect(sorted.shapes.map(shape => shape.text)).toEqual(['⿱十卄'])
  })

  it('sets apart the default figure and the Japanese shape of a character Noto CJK JP draws', () => {
    const sorted = sortForms('邦', bangFigures, bangShapes)
    expect(sorted.figures.map(figure => figure.mj)).toEqual(['MJ026246', 'MJ026244'])
    expect(sorted.shapes.map(shape => shape.text)).toEqual(['⿰⿻㇒𰀁阝'])
    expect(sorted.alike.figures.map(figure => figure.mj)).toEqual(['MJ026242'])
    expect(sorted.alike.shapes.map(shape => shape.text)).toEqual(['⿰丰阝'])
  })

  it('sets apart 臭\'s Japanese shape ⿱自大, a spelling of 臭\'s own 構形', () => {
    const sorted = sortForms('臭', [], [{ text: '⿱自犬', regions: 'GHTKPV' }, { text: '⿱自大', regions: 'J' }])
    expect(sorted.shapes.map(shape => shape.text)).toEqual(['⿱自犬'])
    expect(sorted.alike.shapes.map(shape => shape.text)).toEqual(['⿱自大'])
  })

  it('offers every form of a character drawn from another font', () => {
    // 𠔥 U+20525 is outside Noto CJK JP; Plangothic draws it unlike its default MJ figure.
    const figures = [{ char: '𠔥\u{E0100}', mj: 'MJ031010', default: false }, { char: '𠔥\u{E0108}', mj: 'MJ059329', default: true }]
    expect(sortForms('𠔥', figures, []).figures).toEqual(figures)
  })

  it('knows the ranges both faces take from Noto CJK JP', () => {
    expect(['㐀', '䶵', '一', '鿯'].map(notoDraws)).toEqual([true, true, true, true])
    expect(['䶶', '鿰', '𠀀', '邦\u{E0103}'].map(notoDraws)).toEqual([false, false, false, false])
  })
})
