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
