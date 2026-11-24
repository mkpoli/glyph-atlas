import { describe, expect, it, mock } from 'bun:test'
import LABELS from '../../cloudflare/src/graphemeLabels.json'

mock.module('../src/lib/i18n.svelte.js', () => ({ t: (key, values) => `${key} ${values.char} ${values.codePoint}`, formatDate: () => '', localize: path => path, locale: () => 'en' }))
mock.module('../src/lib/client.js', () => ({ request: async () => ({}) }))
const { graphemeLabel, labelParts, labelSize } = await import('../../cloudflare/src/graphemeLabel.ts')
const { bibtex, characterEntry, citationKey, hayagriva, linkedData } = await import('../../cloudflare/src/citation.ts')
const { galleryName, galleryTitle } = await import('../src/lib/citation.js')
const { graphemeAddress } = await import('../src/lib/addresses.js')
const { chronologyName } = await import('../src/lib/chronology.js')

const card = { code_point: 'U+5047', char: '假', grapheme: { code_point: 'U+4EEE', char: '仮' } }

describe('what stands for a grapheme', () => {
  it('is its 旧字体, its head and its 简体字, each once, else its head', () => {
    expect(['図', '竜', '語', '馬', '国', '仮', '説', '線', '弯', '歩'].map(graphemeLabel)).toEqual(['圖図图', '龍竜龙', '語语', '馬马', '國国', '假仮', '説说', '線线', '彎弯', '步歩'])
    expect(['壷', '税'].map(graphemeLabel)).toEqual(['壺壷壶', '税'])
    expect(['図', '語', '後'].map(labelSize)).toEqual([3, 2, 1])
    // The old form or the 简体字 alone, a head with neither or whose forms are another grapheme's, a kana,
    // a description and a key that names nothing.
    for (const text of ['假', '语', '遇', '神', '青', '状', '弁', '閣', '确', 'は', '⿰木木', '', 'toString']) expect(graphemeLabel(text)).toBe(text)
  })
  it('is two or three characters, the head among them, whatever the table holds', () => {
    for (const [head, label] of Object.entries(LABELS)) expect([[2, 3].includes([...label].length), [...label].includes(head)]).toEqual([true, true])
  })
  it('is set with its 简体字 apart, unless that is the head', () => {
    expect(['図', '語', '国', '仮', '弯', 'は', '⿰木木', ''].map(labelParts)).toEqual([{ text: '圖図', simplified: '图' }, { text: '語', simplified: '语' }, { text: '國国', simplified: '' },
      { text: '假仮', simplified: '' }, { text: '彎弯', simplified: '' }, { text: 'は', simplified: '' }, { text: '⿰木木', simplified: '' }, { text: '', simplified: '' }])
  })
  it('names the grapheme\'s page, title and citation, and leaves a character\'s and every address', () => {
    expect([galleryName({ char: '仮', of: 'grapheme' }), galleryName({ char: '仮', of: 'character' })]).toEqual(['假仮', '仮'])
    expect(galleryTitle({ char: '仮', code_point: 'U+4EEE', of: 'grapheme' })).toBe('cite.label.grapheme 假仮 U+4EEE')
    expect(galleryTitle({ char: '仮', code_point: 'U+4EEE', of: 'character' })).toBe('仮 (U+4EEE)')
    expect(characterEntry('grapheme', card)).toEqual({ kind: 'grapheme', id: 'U+4EEE', label: '假仮', key: '仮', codePoint: 'U+4EEE', path: '/grapheme/U+4EEE' })
    expect(characterEntry('form', { code_point: 'U+4EEE', char: '仮' }).label).toBe('仮')
    expect(linkedData(characterEntry('grapheme', card), 'https://glyphatlas.org')).toMatchObject({ name: '仮', alternateName: ['假'], termCode: 'U+4EEE', url: 'https://glyphatlas.org/grapheme/U+4EEE' })
    expect(linkedData(characterEntry('grapheme', { code_point: 'U+56F3', char: '図' }), '')).toMatchObject({ name: '図', alternateName: ['圖', '图'] })
    expect(linkedData(characterEntry('grapheme', { code_point: 'U+306F', char: 'は' }), '')).not.toHaveProperty('alternateName')
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
    const kana = { code_point: 'U+30CD', char: 'ネ', script: 'katakana', grapheme: { code_point: 'U+306D', char: 'ね' } }, of = (card, scope) => { const { label, code_point, script, path } = chronologyName(card, scope); return [label, code_point, script, path] }
    expect([of(card, 'grapheme'), of(card, '')]).toEqual([['假仮', 'U+4EEE', '', '/chronology/U+4EEE?scope=grapheme'], ['假', 'U+5047', undefined, '/chronology/U+5047']])
    expect([of(kana, 'grapheme'), of(kana, '')]).toEqual([['ね', 'U+306D', '', '/chronology/U+306D?scope=grapheme'], ['ネ', 'U+30CD', 'katakana', '/chronology/U+30CD']])
  })
})
