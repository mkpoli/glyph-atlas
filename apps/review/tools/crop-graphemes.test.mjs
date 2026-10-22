import { describe, expect, it } from 'bun:test'
import { claimedGrapheme, withGraphemeClaim } from '../src/lib/identity.js'

const AFTER = 'U+5F8C', QUEEN = 'U+540E'
const crop = { id: 'crop', label: '后', grapheme: QUEEN, grapheme_claim: null }
const slot = status => ({ status, values: [{ grapheme: AFTER, char: '後', by: ['reader'] }], by: ['reader'] })

describe('a crop\'s grapheme claim', () => {
  it('files the crop under the grapheme it holds, and under its character\'s once none does', () => {
    const filed = withGraphemeClaim(crop, slot('asserted'), QUEEN)
    expect([filed.grapheme, claimedGrapheme(filed)]).toEqual([AFTER, AFTER])
    expect(withGraphemeClaim(filed, null, QUEEN).grapheme).toBe(QUEEN)
    const disputed = withGraphemeClaim(crop, slot('disputed'), QUEEN)
    expect([disputed.grapheme, claimedGrapheme(disputed)]).toEqual([QUEEN, null])
    expect(withGraphemeClaim(crop, null).grapheme).toBe(QUEEN)
  })
})
