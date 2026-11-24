import { describe, expect, it } from 'bun:test'
import { categoryOf } from '../../cloudflare/src/category.ts'
import { gatherGraphemes, rowNamed, tileOpens } from '../src/lib/graphemeGroups.js'

// 大 with its small seal character 𾶱, 民 with two of its own, and は with ハ, as the catalogue and the
// corpus count them.
const DAI = '\u{3EDB1}', MIN = ['\u{3F54D}', '\u{3F54E}']
const categories = [{ label: 'は', grapheme: 'U+306F', total: 5, flagged: 1, hard: 1 }, { label: 'ハ', grapheme: 'U+306F', total: 2, flagged: 0, hard: 0 },
  { label: '大', grapheme: 'U+5927', total: 4, flagged: 0, hard: 1 }]
const corpus = [['大', 'U+5927', 30], [DAI, 'U+5927', 2], [MIN[0], 'U+6C11', 1], [MIN[1], 'U+6C11', 1], ['民', 'U+6C11', 7]]
const gathered = filter => gatherGraphemes({ categories, corpus, ...(filter ? { inScript: text => categoryOf(text) === filter } : {}) })
const shown = groups => groups.map(g => [g.key, g.char, g.alone, g.count, g.members.map(m => m.label)])

describe('the graphemes a collection lists', () => {
  it('gather every form of a grapheme', () => {
    expect(shown(gathered())).toEqual([['U+306F', 'は', false, 7, ['は', 'ハ']], ['U+5927', '大', false, 36, ['大', DAI]],
      ['U+6C11', '民', false, 9, ['民', ...MIN]]])
  })
  it('hold one script under its filter, and the characters of a grapheme another script heads each alone', () => {
    expect(shown(gathered('kana'))).toEqual([['U+306F', 'は', false, 7, ['は', 'ハ']]])
    expect(shown(gathered('kanji'))).toEqual([['U+5927', '大', false, 34, ['大']], ['U+6C11', '民', false, 7, ['民']]])
    const seals = gathered('seal')
    expect(shown(seals)).toEqual([['U+3EDB1', DAI, true, 2, [DAI]], ['U+3F54D', MIN[0], true, 1, [MIN[0]]], ['U+3F54E', MIN[1], true, 1, [MIN[1]]]])
    expect(seals.map(g => g.grapheme)).toEqual(['U+5927', 'U+6C11', 'U+6C11'])
  })
  it('count the crops waiting for a person in the Flagged view', () => {
    expect(gatherGraphemes({ categories, flagged: true }).map(g => [g.char, g.count, g.local, g.corpus])).toEqual([['は', 2, 2, 0], ['大', 1, 1, 0]])
  })
  it('open their own gallery or narrow the listing by their own key, so the tile chosen is the one marked', () => {
    const [dai, ...min] = gathered('seal')
    expect(min.map(g => tileOpens(g, true))).toEqual(MIN.map((_, i) => ({ gallery: ['U+3F54D', 'U+3F54E'][i], of: 'character' })))
    expect(tileOpens(gathered('kanji')[0], true)).toEqual({ gallery: 'U+5927', of: 'grapheme' })
    expect([tileOpens(dai, false), tileOpens({ ...dai, corpus: 0 }, true)]).toEqual([{ narrow: 'U+3EDB1' }, { narrow: 'U+3EDB1' }])
    expect(tileOpens(gathered('kana')[0], true)).toEqual({ narrow: 'U+306F' })
  })
  it('keep what claims file elsewhere with the grapheme claimed, each label once in a group', () => {
    // 𽀅 of 叓, a crop of it claimed as 吏 (most of those flagged among them), and か claimed as 加.
    const LI = '\u{3D005}', rows = [{ label: LI, grapheme: 'U+540F', total: 1, flagged: 1, hard: 0 }, { label: LI, grapheme: 'U+53D3', total: 3, flagged: 0, hard: 0 },
      { label: 'か', grapheme: 'U+52A0', total: 1, flagged: 0, hard: 0 }, { label: 'か', grapheme: 'U+304B', total: 4, flagged: 0, hard: 0 }]
    const under = (filter, more = {}) => gatherGraphemes({ categories: rows, corpus: [[LI, 'U+53D3', 2]], inScript: text => categoryOf(text) === filter, ...more })
    expect(shown(under('seal'))).toEqual([['U+540F', '吏', false, 1, [LI]], ['U+3D005', LI, true, 5, [LI]]])
    expect(shown(under('seal', { flagged: true, corpus: [] })).map(g => g[0])).toEqual(['U+540F', 'U+3D005'])
    expect(shown(under('kana'))).toEqual([['U+52A0', '加', false, 1, ['か']], ['U+304B', 'か', false, 4, ['か']]])
    expect(shown(under('kana', { categories: [{ label: 'か', grapheme: 'U+52A0', total: 1, flagged: 0, hard: 0 }] })).map(g => g[0])).toEqual(['U+52A0'])
  })
  it('name the flagged corpus rows of a grapheme, and of one character under a member\u2019s key', () => {
    const row = { label: '\u{3D307}', grapheme: 'U+6625' }
    expect([rowNamed(row, 'U+6625'), rowNamed(row, 'U+3D307'), rowNamed(row, 'U+8405'), rowNamed({ label: '\u{3D307}' }, 'U+3D307')]).toEqual([true, true, false, true])
  })
})
