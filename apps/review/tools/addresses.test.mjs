import { describe, expect, it, mock } from 'bun:test'

mock.module('../src/lib/i18n.svelte.js', () => ({ t: key => key }))
const { canonicalAddress, characterAddress, expandFor, galleryAddress, galleryPage, graphemeAddress, soleForm } = await import('../src/lib/addresses.js')

const member = (char, count = 0, corpus_count = 0) => ({ char, code_point: 'U+' + char.codePointAt(0).toString(16).toUpperCase(), count, corpus_count })
const grapheme = (members, count, corpus_count) => ({ ...members[0], members, count, corpus_count })
const KA = grapheme([member('仮', 63, 7), member('假', 4, 48), member('𪝒')], 67, 144)
const GU = grapheme([member('遇', 26, 42), member('𨔆'), member('𠓱')], 26, 42)
const ka = { code_point: 'U+5047', char: '假', grapheme: KA }, gu = { code_point: 'U+9047', char: '遇', grapheme: GU }

describe('an address', () => {
  it('names one subject: a grapheme, a character, or a character with its variants', () => {
    expect(expandFor('grapheme')).toBe('grapheme')
    expect(expandFor('character')).toBe('none')
    expect(expandFor('character', 'variants')).toBe('variants')
  })
  it('reads no other scope, and none on a grapheme', () => {
    for (const scope of ['family', 'exact', '']) expect(expandFor('character', scope)).toBe('none')
    expect(expandFor('grapheme', 'variants')).toBe('grapheme')
  })
  it('is the gallery\'s: the grapheme\'s under its head, the character\'s under its own', () => {
    expect(galleryAddress(ka, 'grapheme', { style: 'cursive', years: '1601-1700' })).toBe('/grapheme/U+4EEE?style=cursive&years=1601-1700')
    expect(galleryAddress(ka, 'none', { order: 'book' })).toBe('/character/U+5047?order=book')
    expect(galleryAddress(ka, 'variants')).toBe('/character/U+5047?scope=variants')
    expect(galleryAddress({ code_point: 'U+9047', char: '遇' }, 'grapheme')).toBe('/grapheme/U+9047')
  })
  it('of a grapheme of more than one character narrows the collection', () => {
    expect(graphemeAddress('U+4EEE', '仮')).toBe('/grapheme/U+4EEE')
    expect(graphemeAddress('U+304B U+309A', 'か゚')).toBe('/?grapheme=U%2B304B+U%2B309A')
    expect(characterAddress('U+304B U+309A')).toBe('/character/U+304B-U+309A')
  })
  it('is read back as what it names', () => {
    expect(galleryPage('/grapheme/U+4EEE')).toEqual({ of: 'grapheme', codePoint: 'U+4EEE' })
    expect(galleryPage('/character/U+304B-U+309A')).toEqual({ of: 'character', codePoint: 'U+304B U+309A' })
    for (const path of ['/', '/character/仮', '/forms/U+4EEE', '/grapheme/U+4EEE/x']) expect(galleryPage(path)).toBe(null)
  })
})

describe('the address search engines keep', () => {
  it('is a page\'s own where its gallery is its own', () => {
    expect(canonicalAddress(ka, 'grapheme')).toBe('/grapheme/U+4EEE')
    expect(canonicalAddress(ka, 'none')).toBe('/character/U+5047')
    expect(canonicalAddress(ka, 'variants')).toBe('/character/U+5047')
  })
  it('of a grapheme is its one form\'s where that form holds every crop of it', () => {
    expect(canonicalAddress(gu, 'grapheme')).toBe('/character/U+9047')
    expect(canonicalAddress({ code_point: 'U+9047', char: '遇', grapheme: grapheme([member('遇'), member('𨔆', 2)], 2, 0) }, 'grapheme')).toBe('/character/U+28506')
  })
  it('of a grapheme stays its own where crops are filed under it alone, or no form holds any', () => {
    expect(soleForm(grapheme(GU.members, 26, 43))).toBe(null)
    expect(soleForm(grapheme([member('遇'), member('𨔆')], 0, 0))).toBe(null)
    expect(soleForm(grapheme([member('遇')], 0, 3))).toBe(null)
    expect(soleForm(grapheme([member('遇')], 0, 0)).char).toBe('遇')
    expect(soleForm({ char: '遇', code_point: 'U+9047' }).char).toBe('遇')
  })
})
