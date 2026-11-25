import { describe, expect, it } from 'bun:test'
import { NO_FORMS, barKey, foldGroups, graphemeChips, listedOf } from '../src/lib/graphemeBar.js'
import { chipsOf, groupForms } from '../src/lib/formOffers.js'

// 図's family, cut: 圖 has two regional shapes and a figure, 图 a loan, 啚 is a variant of 圖 outside it, and
// 圗 and 𠚎 are given one shape, which is neither's.
const FIGURE = '圖\u{E0101}', SEAL = '\u{3D1A0}'
const found = {
  head: '図',
  members: [{ char: '図', code_point: 'U+56F3', shapes: [{ text: '⿴囗⿱⺀㐅', regions: 'J', base: '図' }, { text: '⿴囗⿱⺀乂', regions: 'GT', base: '図' }] },
    { char: '圖', code_point: 'U+5716', shapes: [{ text: '⿴囗啚', regions: 'GTJKPV', base: '圖' }, { text: '⿴囗⿱口㐭', regions: 'H', base: '圖' }] },
    { char: '圗', code_point: 'U+5717', shapes: [{ text: '⿱口回', regions: 'G', base: null }] },
    { char: '𠚎', code_point: 'U+2068E', shapes: [{ text: '⿱口回', regions: 'T', base: null }] },
    { char: SEAL, code_point: 'U+3D1A0', script: 'seal', shapes: [] }, { char: '图', code_point: 'U+56FE', shapes: [] }],
  variants: [{ char: '囶', code_point: 'U+56F6', via: ['图'] }, { char: '啚', code_point: 'U+555A', via: ['圖'] }],
  related: [{ char: '度', code_point: 'U+5EA6', via: ['圖'] }, { char: '徒', code_point: 'U+5F92', via: ['图'] }],
  derived: [{ char: '⿴囗⿱二乂', code_point: null, encoded: false, route: [['⺀', '二']], via: ['図'] }, { char: '⿴囗冬', code_point: null, encoded: false, route: [], via: ['图'] }],
  forms: [{ char: '⿴囗斗' }, { char: '圗\u{E0100}' }, { char: '啚\u{E0100}' }],
}
const figures = new Map([['圖', [{ char: '圖\u{E0100}', mj: 'MJ000001', default: true, ids: '⿴囗啚' }, { char: FIGURE, mj: 'MJ000002', default: false, ids: null }]]])
const bar = focal => graphemeChips(found, figures, focal)
const of = list => list.map(chip => chip.char)

describe('the bar of a whole grapheme', () => {
  it('holds every member with its figures and shapes, then the variants and what its crops carry', () => {
    const chips = of(bar(['図']).chips)
    expect(chips.slice(0, 5)).toEqual(['図', '⿴囗⿱⺀乂', '圖', FIGURE, '⿴囗⿱口㐭'])
    expect(chips.slice(-6)).toEqual([SEAL, '囶', '啚', '⿴囗斗', '圗\u{E0100}', '啚\u{E0100}'])
    // The figure and the shape the site's face draws a member as stand for the member.
    expect(of(bar(['図']).alike)).toEqual(expect.arrayContaining(['圖\u{E0100}', '⿴囗⿱⺀㐅', '⿴囗啚']))
  })
  it('puts the characters its source names it by first, then the head, and their variants', () => {
    const { chips, focal } = bar(['圖', 'Unassigned'])
    expect(focal).toEqual(['圖', '図'])
    expect(of(chips).slice(0, 5)).toEqual(['圖', FIGURE, '⿴囗⿱口㐭', '図', '⿴囗⿱⺀乂'])
    expect(of(chips).indexOf('啚')).toBeLessThan(of(chips).indexOf('囶'))
  })
  it('names the member each chip assigns, and none where two share it or it is outside', () => {
    const { bases } = bar(['図'])
    expect(['図', '圖', FIGURE, '⿴囗⿱口㐭', '⿴囗⿱⺀乂', '圗\u{E0100}', SEAL].map(char => bases.get(char))).toEqual(['図', '圖', '圖', '圖', '図', '圗', SEAL])
    // Named by the form left out too, which the reader may draw.
    expect(bases.get('⿴囗啚')).toBe('圖')
    expect(['⿱口回', '啚', '啚\u{E0100}', '囶', '⿴囗斗'].map(char => bases.get(char))).toEqual([null, null, null, null, null])
  })
  it('leaves the loans, the related and the derived of its named members to the picker', () => {
    const { chips, related, derived } = bar(['図', '圖'])
    expect([of(related), of(derived)]).toEqual([['度'], ['⿴囗⿱二乂']])
    expect(of(chips)).not.toContain('度')
    expect(derived[0].routes).toEqual([[{ was: '⺀', became: '二' }]])
    expect(bar(['図', '圖']).shaped).toEqual(['図', '圖'])
  })
})

describe('what a bar lists before it is read', () => {
  const chars = ({ ready, listed }) => [ready, (listed.chips ?? listed.members).map(form => form.char)]
  const bar = (key, chips) => ({ ...NO_FORMS, key, chips: chips.map(char => ({ char })) })
  const assigned = barKey('a', '図', false, ['図', '図']), whole = barKey('a', '図', true, ['図', '図'])
  it('is the character a crop has, and nothing for one with none', () => {
    const nothing = { key: null, ...NO_FORMS }
    expect([chars(listedOf(nothing, assigned, '図', false)), chars(listedOf(nothing, whole, '図', true))]).toEqual([[true, ['図']], [false, []]])
  })
  it('is never the bar of a crop of the other kind', () => {
    expect(chars(listedOf(bar(assigned, ['図', '圖']), whole, '図', true))).toEqual([false, []])
    expect(chars(listedOf(bar(whole, ['圖', '図']), assigned, '図', false))).toEqual([true, ['図']])
    expect(chars(listedOf(bar(whole, ['圖', '図']), whole, '図', true))).toEqual([true, ['圖', '図']])
  })
  it('is never another crop’s where it has no character, or that of another name its source gives', () => {
    for (const next of [barKey('b', '図', true, ['図', '図']), barKey('a', '図', true, ['圖', '図']), barKey('a', '所', true, ['図', '図'])])
      expect(chars(listedOf(bar(whole, ['圖', '図']), next, '図', true))).toEqual([false, []])
    // A character's bar is the same from crop to crop.
    expect(barKey('b', '図', false, ['圖', '図'])).toBe(assigned)
  })
})

describe('a bar of many forms', () => {
  // 60 forms under 20 構形, three each; the named one is the 15th.
  const list = Array.from({ length: 60 }, (_, i) => ({ char: String.fromCodePoint(0x4E00 + i) }))
  const structureOf = new Map(list.map(({ char }, i) => [char, { id: 'st' + Math.floor(i / 3), label: 'L' + Math.floor(i / 3) }]))
  const groups = groupForms(list, { structureOf })
  it('folds whole 構形 away from the end, never one that is named or chosen', () => {
    const { shown, hidden } = foldGroups(groups, { keep: new Set([list[44].char]) })
    expect([chipsOf(shown).length, chipsOf(hidden).length]).toEqual([39, 21])
    expect(shown.map(group => group.id)).toEqual([...Array.from({ length: 12 }, (_, i) => 'st' + i), 'st14'])
    expect(hidden.map(group => group.id)).toEqual(['st12', 'st13', 'st15', 'st16', 'st17', 'st18', 'st19'])
  })
  it('folds away no 構形 chosen or held alone', () => {
    const { shown } = foldGroups(groups, { structures: new Set(['st18']) })
    expect(shown.map(group => group.id)).toEqual([...Array.from({ length: 12 }, (_, i) => 'st' + i), 'st18'])
  })
  it('folds nothing up to forty', () => {
    expect(foldGroups(groupForms(list.slice(0, 40), { structureOf })).hidden).toEqual([])
  })
})

describe('the bar of a crop given another character', () => {
  it('lists that character until its grapheme is read', () => {
    const { ready, listed } = listedOf({ key: 'other' }, 'key', '転', true, '転')
    expect([ready, listed.chips.map(chip => [chip.char, chip.base]), listed.bases.get('転')]).toEqual([false, [['転', '転']], '転'])
    expect(listedOf({ key: 'other' }, 'key', '転', true).listed.members).toEqual([])
  })
})
