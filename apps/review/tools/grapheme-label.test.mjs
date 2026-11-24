import { describe, expect, it, mock } from 'bun:test'
import LABELS from '../../cloudflare/src/graphemeLabels.json'

mock.module('../src/lib/i18n.svelte.js', () => ({ t: (key, values) => `${key} ${values.char} ${values.codePoint}`, formatDate: () => '', localize: path => path, locale: () => 'en' }))
const { graphemeLabel } = await import('../../cloudflare/src/graphemeLabel.ts')
const { characterEntry, linkedData } = await import('../../cloudflare/src/citation.ts')
const { galleryName, galleryTitle } = await import('../src/lib/citation.js')
const { graphemeAddress } = await import('../src/lib/addresses.js')

const card = { code_point: 'U+5047', char: '假', grapheme: { code_point: 'U+4EEE', char: '仮' } }

describe('what stands for a grapheme', () => {
  it('is its 旧字体 and 新字体 where a 新字体 heads it, else its head', () => {
    expect(['仮', '国', '竜', '発', '鶏'].map(graphemeLabel)).toEqual(['假仮', '國国', '龍竜', '發発', '鷄鶏'])
    // The old form alone, a head that is no 新字体, a kana, a description and a key that names nothing.
    for (const text of ['假', '遇', '神', 'は', '⿰木木', '', 'toString']) expect(graphemeLabel(text)).toBe(text)
  })
  it('is two characters of two, whatever the table holds', () => {
    for (const [head, old] of Object.entries(LABELS)) expect([...graphemeLabel(head)]).toEqual([old, head])
  })
  it('names the grapheme\'s page, title and citation, and leaves a character\'s and every address', () => {
    expect([galleryName({ char: '仮', of: 'grapheme' }), galleryName({ char: '仮', of: 'character' })]).toEqual(['假仮', '仮'])
    expect(galleryTitle({ char: '仮', code_point: 'U+4EEE', of: 'grapheme' })).toBe('cite.label.grapheme 假仮 U+4EEE')
    expect(galleryTitle({ char: '仮', code_point: 'U+4EEE', of: 'character' })).toBe('仮 (U+4EEE)')
    expect(characterEntry('grapheme', card)).toEqual({ kind: 'grapheme', id: 'U+4EEE', label: '假仮', codePoint: 'U+4EEE', path: '/grapheme/U+4EEE' })
    expect(characterEntry('form', { code_point: 'U+4EEE', char: '仮' }).label).toBe('仮')
    expect(linkedData(characterEntry('grapheme', card), 'https://glyphatlas.org')).toMatchObject({ name: '假仮', termCode: 'U+4EEE', url: 'https://glyphatlas.org/grapheme/U+4EEE' })
    expect(graphemeAddress('U+4EEE', '仮')).toBe('/grapheme/U+4EEE')
  })
})
