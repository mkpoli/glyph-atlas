import { describe, expect, it } from 'bun:test'
import { categoryOf } from '../../cloudflare/src/category.ts'
import { gatherGraphemes } from '../src/lib/graphemeGroups.js'

// 大 with its small seal character 𾷱, 民 with two of its own, and は with ハ, as the catalogue and the
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
})
