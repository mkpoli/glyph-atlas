import { describe, expect, it } from 'bun:test'
import { cardTargets } from '../src/lib/cardTiers.js'

// The card the site serves for 說 (U+8AAA), cut to what the target list reads.
const card = {
  char: '說', code_point: 'U+8AAA',
  grapheme: { char: '説', code_point: 'U+8AAC', members: [
    { char: '説', code_point: 'U+8AAC' }, { char: '說', code_point: 'U+8AAA' }, { char: '说', code_point: 'U+8BF4' },
    { char: '𧧘', code_point: 'U+279D8' }, { char: '說', code_point: 'U+F96F' }, { char: '瀞', code_point: 'U+FA9B' },
  ] },
  variants: {
    items: [{ char: '説', code_point: 'U+8AAC' }, { char: '虔', code_point: 'U+8654' }],
    related: [{ char: '说', code_point: 'U+8BF4', written: true }, { char: '悅', code_point: 'U+6085' }],
    derived: [{ char: '䛐', code_point: 'U+46D0' }],
    forms: [{ char: '⿰言兊', form: '⿰言兊' }],
  },
}

describe("a character card's bulk targets", () => {
  it('list the grapheme, its forms, written variants and described forms once each', () => {
    expect(cardTargets(card).map(target => target.char)).toEqual(['説', '說', '说', '𧧘', '虔', '⿰言兊'])
  })
  it('leave out compatibility ideographs, related characters and the derived tier', () => {
    const chars = cardTargets(card).map(target => target.char)
    for (const char of ['說', '瀞', '悅', '䛐']) expect(chars).not.toContain(char)
  })
  it('give a form with no grapheme of its own the graphemes it writes', () => {
    const own = { char: '𠮮', code_point: 'U+20BAE', variants: { own: false, writes: [{ char: '只', code_point: 'U+53EA' }] } }
    expect(cardTargets(own).map(target => target.char)).toEqual(['只', '𠮮'])
  })
  it('are none without a card', () => expect(cardTargets(null)).toEqual([]))
})
