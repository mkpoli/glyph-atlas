import { describe, expect, it, mock } from 'bun:test'
import LABELS from '../../cloudflare/src/graphemeLabels.json'

mock.module('../src/lib/i18n.svelte.js', () => ({ t: (key, values) => `${key} ${values.char} ${values.codePoint}`, formatDate: () => '', localize: path => path, locale: () => 'en' }))
mock.module('../src/lib/client.js', () => ({ request: async () => ({}) }))
const { graphemeLabel } = await import('../../cloudflare/src/graphemeLabel.ts')
const { bibtex, characterEntry, citationKey, hayagriva, linkedData } = await import('../../cloudflare/src/citation.ts')
const { galleryName, galleryTitle } = await import('../src/lib/citation.js')
const { graphemeAddress } = await import('../src/lib/addresses.js')
const { chronologyName } = await import('../src/lib/chronology.js')

const card = { code_point: 'U+5047', char: '假', grapheme: { code_point: 'U+4EEE', char: '仮' } }

describe('what stands for a grapheme', () => {
  it('is its 旧字体 and 新字体 where a 新字体 heads it, else its head', () => {
    expect(['仮', '国', '竜', '発', '郷'].map(graphemeLabel)).toEqual(['假仮', '國国', '龍竜', '發発', '鄕郷'])
    expect(['歩', '録', '闘', '麺'].map(graphemeLabel)).toEqual(['步歩', '錄録', '鬭闘', '麵麺'])
    // The old form alone, a head that is no 新字体 or whose older form is another grapheme's, a kana, a description and a key that names nothing.
    for (const text of ['假', '遇', '神', '青', '状', '弁', 'は', '⿰木木', '', 'toString']) expect(graphemeLabel(text)).toBe(text)
  })
  it('is two characters of two, whatever the table holds', () => {
    for (const [head, old] of Object.entries(LABELS)) expect([...graphemeLabel(head)]).toEqual([old, head])
  })
  it('names the grapheme\'s page, title and citation, and leaves a character\'s and every address', () => {
    expect([galleryName({ char: '仮', of: 'grapheme' }), galleryName({ char: '仮', of: 'character' })]).toEqual(['假仮', '仮'])
    expect(galleryTitle({ char: '仮', code_point: 'U+4EEE', of: 'grapheme' })).toBe('cite.label.grapheme 假仮 U+4EEE')
    expect(galleryTitle({ char: '仮', code_point: 'U+4EEE', of: 'character' })).toBe('仮 (U+4EEE)')
    expect(characterEntry('grapheme', card)).toEqual({ kind: 'grapheme', id: 'U+4EEE', label: '假仮', key: '仮', codePoint: 'U+4EEE', path: '/grapheme/U+4EEE' })
    expect(characterEntry('form', { code_point: 'U+4EEE', char: '仮' }).label).toBe('仮')
    expect(linkedData(characterEntry('grapheme', card), 'https://glyphatlas.org')).toMatchObject({ name: '假仮', termCode: 'U+4EEE', url: 'https://glyphatlas.org/grapheme/U+4EEE' })
    expect(graphemeAddress('U+4EEE', '仮')).toBe('/grapheme/U+4EEE')
  })
  it('leaves the key a bibliography cites a grapheme by', () => {
    const entry = characterEntry('grapheme', card), day = [2026, 11, 24]
    expect(citationKey(entry)).toBe('glyphatlas-仮-grapheme')
    expect(citationKey(characterEntry('grapheme', { code_point: 'U+306F', char: 'は' }))).toBe('glyphatlas-は-grapheme')
    expect(bibtex(entry, 'https://glyphatlas.org', day)).toStartWith('@misc{glyphatlas-仮-grapheme,')
    expect(hayagriva(entry, 'https://glyphatlas.org', day)).toStartWith('glyphatlas-仮-grapheme:')
  })
  it('names a time axis by what its scope is of, with that one\'s code point, and a script for a character alone', () => {
    const kana = { code_point: 'U+30CD', char: 'ネ', script: 'katakana', grapheme: { code_point: 'U+306D', char: 'ね' } }
    expect([chronologyName(card, 'grapheme'), chronologyName(card, '')]).toEqual([{ label: '假仮', code_point: 'U+4EEE', script: '' }, { label: '假', code_point: 'U+5047', script: undefined }])
    expect([chronologyName(kana, 'grapheme'), chronologyName(kana, '')]).toEqual([{ label: 'ね', code_point: 'U+306D', script: '' }, { label: 'ネ', code_point: 'U+30CD', script: 'katakana' }])
  })
})
