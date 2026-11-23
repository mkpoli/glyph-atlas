import { describe, expect, it } from 'bun:test'
import { notoDraws, sortForms } from '../src/lib/formOffers.js'

// 邦's five Moji_Joho figures as the MJ figure list gives them; MJ026242 is the default.
const bangFigures = [['\u{E0103}', 'MJ026242', true], ['\u{E0104}', 'MJ026246', false], ['\u{E0105}', 'MJ026244', false]]
  .map(([selector, mj, isDefault]) => ({ char: '邦' + selector, mj, default: isDefault }))
// Shapes as /layers/ids gives them.
const bangShapes = [{ text: '⿰丰阝', regions: 'GHTJKV' }, { text: '⿰⿻㇒𰀁阝', regions: 'P' }]
const chouShapes = [{ text: '⿱自犬', regions: 'GHTKPV' }, { text: '⿱自大', regions: 'J' }]

describe('form offers', () => {
  it('sets apart the default figure and the Japanese shape of a character Noto CJK JP draws', () => {
    const sorted = sortForms('邦', bangFigures, bangShapes)
    expect(sorted.figures.map(figure => figure.mj)).toEqual(['MJ026246', 'MJ026244'])
    expect(sorted.shapes.map(shape => shape.text)).toEqual(['⿰⿻㇒𰀁阝'])
    expect(sorted.alike.figures.map(figure => figure.mj)).toEqual(['MJ026242'])
    expect(sorted.alike.shapes.map(shape => shape.text)).toEqual(['⿰丰阝'])
  })

  it('sets apart 臭\'s Japanese shape ⿱自大, a spelling of 臭\'s own 構形', () => {
    const sorted = sortForms('臭', [], chouShapes)
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
