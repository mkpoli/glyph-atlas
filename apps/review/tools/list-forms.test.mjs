import { describe, expect, it, mock } from 'bun:test'

// 臭 and 逸 as the card and /layers/ids give them, cut to what the bar reads. 𦤀 is a variant of 臭; 𨓜
// is a member of 逸's grapheme.
const cards = {
  'U+81ED': { grapheme: { members: [{ char: '臭' }] }, variants: { items: [{ char: '𦤀' }, { char: '臰' }], related: [] } },
  'U+9038': { grapheme: { members: [{ char: '逸' }, { char: '𨓜' }] }, variants: { items: [], related: [] } },
  'U+284DC': { grapheme: { members: [{ char: '逸' }, { char: '𨓜' }] }, variants: { items: [], related: [] } },
}
const shapes = {
  臭: [{ text: '⿱自犬', regions: 'GHTKPV' }, { text: '⿱自大', regions: 'J', chars: ['𦤀'] }],
  逸: [{ text: '⿺辶兔', regions: 'GHTV' }, { text: '⿺辶免', regions: 'J', chars: ['𨓜'] }, { text: '⿺辶⿷免丶', regions: 'KP' }],
}
mock.module('../src/lib/layers.js', () => ({ character: async code => cards[code], ids: async char => ({ shapes: shapes[char] ?? [] }) }))
mock.module('../src/lib/mjFigures.js', () => ({ drawFigure: async () => {}, figuresOf: async () => [] }))
mock.module('../src/lib/client.js', () => ({ request: async () => {}, character: async () => {}, cropPixels: async () => {} }))
const { listForms } = await import('../src/lib/cropForms.js')
const chars = list => list.map(form => form.char)

describe('a character that encodes the J shape again', () => {
  it('leaves the bar as a variant and stays known as alike', async () => {
    const found = await listForms('臭')
    expect([chars(found.members), chars(found.variants), chars(found.related)]).toEqual([['臭'], ['⿱自犬', '臰'], []])
    expect(chars(found.alike)).toEqual(['⿱自大', '𦤀'])
  })

  it('leaves the bar as a member of the grapheme', async () => {
    const found = await listForms('逸')
    expect([chars(found.members), chars(found.alike)]).toEqual([['逸'], ['⿺辶免', '𨓜']])
  })

  it('is offered on a crop written as itself', async () => {
    expect(chars((await listForms('𨓜')).members)).toEqual(['逸', '𨓜'])
  })
})
