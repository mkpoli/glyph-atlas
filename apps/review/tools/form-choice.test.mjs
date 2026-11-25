import { describe, expect, it } from 'bun:test'
import { characterNamed, namingReview } from '../src/lib/formChoice.js'

describe('a form chosen on an assigned crop', () => {
  const of = (form, base, issue = null) => characterNamed({ form, base, character: '図', issue })
  it('names another character of the grapheme, and nothing where it names none or the crop\'s own', () => {
    expect([of('圖', '圖'), of('図', '図'), of('啚', null), of(null, null), of({ target: {} }, null)]).toEqual(['圖', null, null, null, null])
  })
  it('gives way to a wrong character', () => {
    expect(of('圖', '圖', 'character')).toBe(null)
    expect(of('圖', '圖', 'crop')).toBe('圖')
  })
  it('is a form of the wrong character\'s correction while that is the one chosen', () => {
    const corrected = (form, base, written) => characterNamed({ form, base, character: '時', issue: 'character', written })
    expect([corrected('圖\u{E0100}', '圖', '圖'), corrected('⿴囗啚', '圖', '圖'), corrected('圖\u{E0100}', '圖', '図'), corrected('圖', null, '圖')]).toEqual(['圖', '圖', null, null])
  })
})

describe('a form chosen on a crop with no character', () => {
  const of = (form, base, issue, written) => characterNamed({ form, base, character: '', issue, unassigned: true, written })
  it('names its character while that is the one shown chosen', () => {
    expect([of('圖\u{E0100}', '圖', 'character', '圖'), of('⿴囗啚', '圖', 'character', '圖')]).toEqual(['圖', '圖'])
  })
  it('names none once another character is chosen, or the choice taken back', () => {
    expect([of('圖\u{E0100}', '圖', 'character', '図'), of('圖\u{E0100}', '圖', null, ''), of('圖\u{E0100}', '圖', 'merged', '')]).toEqual([null, null, null])
  })
  it('names none where the form is of none, so it is claimed alone', () => {
    expect([of('啚', null, null, ''), of('⿰木木', null, null, ''), of(null, null, 'character', '圖')]).toEqual([null, null, null])
  })
})

describe('the review that names a character and claims its form', () => {
  const crop = { revision: 3, crop_version: 'v1' }, named = { character: '圖', form: '圖\u{E0100}' }
  it('is one save, on the version the reader saw', () => {
    expect(namingReview(crop, named)).toEqual({ revision: 3, verdict: 'wrong', issue: 'character', character: '圖', form: '圖\u{E0100}', crop_version: 'v1' })
  })
  it('carries the box the reader drew, or reports the crop beside it', () => {
    const box = { x: 1, y: 2, w: 3, h: 4 }
    expect(namingReview(crop, { ...named, issues: ['crop'], box })).toMatchObject({ issue: 'character', box })
    expect(namingReview(crop, { ...named, issues: ['crop'] })).toMatchObject({ issue: 'character', bad_crop: true })
    expect(namingReview(crop, { ...named, issues: ['merged', 'crop'], correction: '図圖' })).toMatchObject({ issue: 'merged', correction: '図圖', bad_crop: true })
  })
})
