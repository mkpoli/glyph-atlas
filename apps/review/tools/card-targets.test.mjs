import { describe, expect, it } from 'bun:test'
import { cardTargets, cardTiers, inferred, isSeal, sealsLast, structureForms } from '../src/lib/cardTiers.js'

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

describe("a character card's 字体", () => {
  const chars = list => list.map(v => v.char)
  it('leave out the compatibility ideographs of the family', () => {
    expect(chars(cardTiers(card).members)).toEqual(['説', '說', '说', '𧧘'])
  })
  it('keep one that crops are filed under, and a unified ideograph of the compatibility block', () => {
    const filed = { char: '突', code_point: 'U+7A81', grapheme: { char: '突', code_point: 'U+7A81', members: [
      { char: '突', code_point: 'U+7A81', count: 3 }, { char: '\ufa55', code_point: 'U+FA55', corpus_count: 40 }] } }
    expect(chars(cardTiers(filed).members)).toEqual(['突', '\ufa55'])
    const unified = { char: '崎', code_point: 'U+5D0E', grapheme: { char: '崎', code_point: 'U+5D0E', members: [
      { char: '崎', code_point: 'U+5D0E' }, { char: '\ufa11', code_point: 'U+FA11' }] } }
    expect(chars(cardTiers(unified).members)).toEqual(['崎', '\ufa11'])
  })
  it("keep the card's own character, and drop one from the written and related tiers", () => {
    const own = { ...card, char: '\uf96f', code_point: 'U+F96F' }
    expect(chars(cardTiers(own).members)).toEqual(['説', '說', '说', '𧧘', '\uf96f'])
    const tiers = cardTiers({ char: '練', code_point: 'U+7DF4', variants: {
      items: [{ char: '\uf996', code_point: 'U+F996' }, { char: '练', code_point: 'U+7EC3' }], related: [{ char: '\ufa57', code_point: 'U+FA57' }] } })
    expect([chars(tiers.written), chars(tiers.related)]).toEqual([['练'], []])
  })
})

describe('a small seal character of the grapheme', () => {
  // 萅's card: 𽌇 is a member of 春's family, and the pair's own row is still in the variants.
  const SEAL = '\u{3D307}', pair = char => ({ char, code_point: 'U+' + char.codePointAt(0).toString(16).toUpperCase(), written: false,
    relations: [{ relation: 'small-seal', source: 'unicode-ucd', detail: `${SEAL}→萅 kSEAL_MCJK` }] })
  const grapheme = { char: '春', code_point: 'U+6625', members: [{ char: '春', code_point: 'U+6625' }, { char: SEAL, code_point: 'U+3D307' },
    { char: '萅', code_point: 'U+8405' }] }
  const chun = { char: '萅', code_point: 'U+8405', grapheme, variants: { items: [], related: [pair(SEAL)] } }
  it('is one of its 字体, after every other form, and is listed once', () => {
    // 萶 is written for 春 outside the grapheme, and a described form joins it.
    const { members, seals, written, related } = cardTiers({ ...chun, variants: { ...chun.variants, items: [{ char: '萶', code_point: 'U+8436' }] } })
    expect([members, seals, written].map(list => list.map(v => v.char))).toEqual([['春', '萅'], [SEAL], ['萶']])
    expect(related).toEqual([])
    const targets = cardTargets({ ...chun, variants: { ...chun.variants, items: [{ char: '萶', code_point: 'U+8436' }], forms: [{ char: '⿱𡗗日', form: 'fm:1' }] } })
    expect(targets.map(target => target.char)).toEqual(['春', '萅', '萶', '⿱𡗗日', SEAL])
  })
  it("has its Han characters as its own card's 字体", () => {
    const { members, seals, related, grapheme: head } = cardTiers({ char: SEAL, code_point: 'U+3D307', grapheme, variants: { items: [], related: [pair('萅')] } })
    expect([head.char, members.map(v => v.char), seals.map(v => v.char), related]).toEqual(['春', ['春', '萅'], [SEAL], []])
  })
  it("is in the 構形 row once, a member or still a related character of the reference data the card was read from", () => {
    const row = card => structureForms(cardTiers(card)).map(v => v.char)
    expect(row(chun)).toEqual(['春', '萅', SEAL])
    const before = { ...chun, grapheme: { ...grapheme, members: grapheme.members.filter(v => v.char !== SEAL) } }
    expect(row(before)).toEqual(['春', '萅', SEAL])
    expect(cardTiers(before).related.map(v => v.char)).toEqual([SEAL])
    // A seal character alone until the load, and one a pair is only inferred for, name no other.
    expect(row({ char: SEAL, code_point: 'U+3D307', variants: { related: [pair('萅')] } })).toEqual([SEAL])
    const li = { char: '吏', code_point: 'U+540F', variants: { related: [{ ...pair('\u{3D005}'), relations: [{ relation: 'small-seal', source: 'seal-chain' }] }] } }
    expect(row(li)).toEqual(['吏'])
  })
  it('follows every other form, each part in its own order', () => {
    expect(sealsLast([{ char: SEAL }, { char: '春' }, { char: '\u{3D000}' }, { char: '旾' }]).map(v => v.char)).toEqual(['春', '旾', SEAL, '\u{3D000}'])
  })
})

describe('an inferred small seal pair', () => {
  // 吏's card: 叓 by sources, and the seal character through 叓, which no source states.
  const li = { char: '吏', code_point: 'U+540F', variants: { items: [], related: [
    { char: '叓', code_point: 'U+53D3', written: true, relations: [{ relation: 'shuowen', source: 'opencc', detail: 'SealVariants' }] },
    { char: '\u{3D005}', code_point: 'U+3D005', written: false, relations: [{ relation: 'small-seal', source: 'seal-chain', detail: '吏→叓 SealVariants (opencc) + \u{3D005}→叓 kSEAL_MCJK (unicode-ucd)' }] },
  ] } }
  it('is a related character and never a form of the Han character', () => {
    const { written, related } = cardTiers(li)
    expect(written.map(v => v.char)).toEqual(['叓'])
    expect(related.map(v => v.code_point)).toEqual(['U+3D005'])
    expect(cardTargets(li).map(target => target.code_point)).not.toContain('U+3D005')
  })
  it('is marked inferred only when no source states it', () => {
    expect(li.variants.related.map(inferred)).toEqual([false, true])
    expect(inferred({ relations: [{ source: 'seal-chain' }, { source: 'unicode-ucd' }] })).toBe(false)
    expect(inferred({ relations: [] })).toBe(false)
  })
  it('is told from a Han character by its block', () => {
    expect(['\u{3D000}', '\u{3FC3F}', '叓', '\u{3FC40}', undefined].map(isSeal)).toEqual([true, true, false, false, false])
  })
})
