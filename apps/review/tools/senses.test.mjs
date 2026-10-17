import { describe, expect, it } from 'bun:test'
import { titleOf, wiktionaryLine, yearOf, yearsOf } from '../src/lib/senses.js'

const witness = (year, source = '蝦夷方言藻汐草　坤巻（国立国語研究所）') =>
  ({ id: 'ar:x', source, dating: year ? { composed: { label: year } } : {} })
const URL = 'https://glyphatlas.org/ja/crop/ar%3Ax'

describe("a sense's witnesses", () => {
  it('name the document without its holder, and the year its dating states', () => {
    expect(titleOf('蝦夷方言藻汐草　坤巻（国立国語研究所）')).toBe('蝦夷方言藻汐草　坤巻')
    expect(yearOf(witness('1792'))).toBe('1792')
    expect(yearOf(witness(null))).toBeNull()
    expect(yearOf({ dating: { composed: { label: '1799' }, witness: { label: '1848–55' } } })).toBe('1848–55')
    expect(yearsOf({ witnesses: [witness('1799'), witness('1739'), witness(null)] })).toBe('1739–1799')
    expect(yearsOf({ witnesses: [witness(null)] })).toBeNull()
  })
  it('quote the character alone, as a Japanese 用例 line or an English quote-book', () => {
    expect(wiktionaryLine('ja', '𩹉', witness('1792'), URL)).toBe(`#*'''𩹉'''（『蝦夷方言藻汐草　坤巻』）〔1792年〕[${URL}]`)
    expect(wiktionaryLine('ja', '𩹉', witness('嘉永年間'), URL)).toBe(`#*'''𩹉'''（『蝦夷方言藻汐草　坤巻』）〔嘉永年間〕[${URL}]`)
    expect(wiktionaryLine('en', '𩹉', witness('1792'), URL))
      .toBe(`#* {{quote-book|ja|year=1792|title=蝦夷方言藻汐草　坤巻|url=${URL}|passage='''𩹉'''}}`)
    expect(wiktionaryLine('en', '𩹉', witness(null, null), URL)).toBe(`#* {{quote-book|ja|url=${URL}|passage='''𩹉'''}}`)
  })
})
