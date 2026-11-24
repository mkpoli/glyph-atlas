import { describe, expect, it, mock } from 'bun:test'

// 臭, 器 and 逸 as the card, /layers/ids and the MJ figures give them, cut to what the bar reads. 𦤀 is a
// variant of 臭 and 𠾖 of 器; 𨓜 is a member of 逸's grapheme.
const cards = {
  'U+81ED': { grapheme: { members: [{ char: '臭' }] }, variants: { items: [{ char: '𦤀' }, { char: '臰' }], related: [] } },
  'U+5668': { grapheme: { members: [{ char: '器' }] }, variants: { items: [{ char: '噐' }, { char: '𠾖' }], related: [{ char: '氣' }] } },
  'U+9038': { grapheme: { members: [{ char: '逸' }, { char: '𨓜' }] }, variants: { items: [], related: [] } },
  // 萅 and its seal character 𽌇, of 春's family, the seal character given before a Han form.
  'U+8405': { grapheme: { members: [{ char: '春' }, { char: '\u{3D307}' }, { char: '萅' }] },
    variants: { items: [], related: [{ char: '\u{3D307}', written: false, relations: [{ relation: 'small-seal', source: 'unicode-ucd' }] }] } },
  'U+3D307': { grapheme: { members: [{ char: '春' }, { char: '萅' }, { char: '\u{3D307}' }] },
    variants: { items: [], related: [{ char: '萅', written: false, relations: [{ relation: 'small-seal', source: 'unicode-ucd' }] }] } },
  'U+284DC': { grapheme: { members: [{ char: '逸' }, { char: '𨓜' }] }, variants: { items: [], related: [] } },
}
const shapes = {
  臭: [{ text: '⿱自犬', regions: 'GHTKPV' }, { text: '⿱自大', regions: 'J', chars: ['𦤀'] }],
  器: [{ text: '⿳吅犬吅', regions: 'GHTKPV' }, { text: '⿳吅大吅', regions: 'J', chars: ['𠾖'] }],
  逸: [{ text: '⿺辶兔', regions: 'GHTV' }, { text: '⿺辶免', regions: 'J', chars: ['𨓜'] }, { text: '⿺辶⿷免丶', regions: 'KP' }],
}
mock.module('../src/lib/layers.js', () => ({ character: async code => cards[code], ids: async char => ({ shapes: shapes[char] ?? [] }) }))
const figures = { 器: [['器\u{E0102}', 'MJ008683', true, '⿳吅大吅'], ['器\u{E0103}', 'MJ030252', false, '⿳吅犬吅'],
  ['器\u{E0104}', 'MJ057164', false, '⿱⿱𫩏大𫩏'], ['器\u{E0105}', 'MJ058033', false, '⿳𫩏犬𫩏']] }
mock.module('../src/lib/mjFigures.js', () => ({ drawFigure: async () => {},
  figuresOf: async char => (figures[char] ?? []).map(([text, mj, isDefault, ids]) => ({ char: text, mj, default: isDefault, ids })) }))
const sent = []
mock.module('../src/lib/client.js', () => ({ request: async (path, body) => { sent.push({ path, body }); return { form: null } },
  character: async id => ({ id, crop_version: 'v2' }), cropPixels: () => ({}) }))
const { baseOf, formSaved, listForms, setForm } = await import('../src/lib/cropForms.js')
const chars = list => list.map(form => form.char)

describe('a character that encodes the J shape again', () => {
  it('leaves the bar as a variant and stays known as alike', async () => {
    const found = await listForms('臭')
    expect([chars(found.members), chars(found.variants), chars(found.related)]).toEqual([['臭'], ['⿱自犬', '臰'], []])
    expect(chars(found.alike)).toEqual(['⿱自大', '𦤀'])
  })

  it('leaves the figure of a shape and the bare character as the only forms of 器', async () => {
    const found = await listForms('器')
    expect([chars(found.members), chars(found.variants)]).toEqual([['器'], ['器\u{E0103}', '器\u{E0104}', '器\u{E0105}', '噐']])
    expect(chars(found.alike)).toEqual(['器\u{E0102}', '⿳吅犬吅', '⿳吅大吅', '𠾖'])
  })

  it('is the character choosing it names, from either of the two', async () => {
    for (const [written, form] of [['逸', '𨓜'], ['𨓜', '逸']]) expect(baseOf(form, await listForms(written))).toBe(form)
    // A variant of another grapheme, a shape and a figure name no character.
    for (const form of ['氣', '噐', '⿳吅大吅', '器\u{E0103}']) expect(baseOf(form, await listForms('器'))).toBe(null)
  })

  it('leaves the bar as a member of the grapheme', async () => {
    const found = await listForms('逸')
    expect([chars(found.members), chars(found.alike)]).toEqual([['逸'], ['⿺辶免', '𨓜']])
  })

  it('is offered on a crop written as itself', async () => {
    expect(chars((await listForms('𨓜')).members)).toEqual(['逸', '𨓜'])
  })
})

describe('a small seal character of the grapheme', () => {
  it('is the character choosing it names, as any member is', async () => {
    expect(baseOf('\u{3D307}', await listForms('萅'))).toBe('\u{3D307}')
  })
  it('is a member, after the Han forms, and is not offered again as related', async () => {
    const found = await listForms('萅')
    expect([chars(found.members), chars(found.variants), chars(found.related)]).toEqual([['春', '萅', '\u{3D307}'], [], []])
  })
  it("offers its Han characters as its grapheme's forms", async () => {
    const found = await listForms('\u{3D307}')
    expect([chars(found.members), chars(found.related)]).toEqual([['春', '萅', '\u{3D307}'], []])
  })
})

describe('a form claimed alone', () => {
  it('is one request on the version the reader saw, and no read before it', async () => {
    sent.length = 0
    const { crop } = await setForm({ id: 'x', crop_version: 'v1', revision: 1, written_character: '器' }, '噐')
    expect(sent.map(({ path, body }) => [path, body.crop_version, body.form, Object.keys(body).sort().join()])).toEqual([['/atlas/characters/x/form', 'v1', '噐', 'crop_version,form,id']])
    expect(crop).toMatchObject({ crop_version: 'v1', form: null })
    await setForm({ id: 'x', crop_version: 'v1', revision: 1 }, { target: { char: '器' } })
    expect(sent[1]).toMatchObject({ path: '/atlas/characters/x/structure', body: { crop_version: 'v1', char: '器' } })
  })
  it('is a new claim each time it is chosen', async () => {
    sent.length = 0
    const crop = { id: 'x', crop_version: 'v1', revision: 1, written_character: '器' }
    for (const form of ['噐', null, '噐']) await setForm(crop, form)
    expect(new Set(sent.map(({ body }) => body.id)).size).toBe(3)
  })
})

describe('the forms read of a grapheme', () => {
  const had = text => ({ id: 'x', crop_version: 'v1', form: { status: 'asserted', values: [{ form: 'f', text }] } })
  it('are read again once a save takes a described form from a crop, whatever it claims instead', async () => {
    const first = listForms('器')
    formSaved(had('噐'), '器')
    expect(listForms('器')).toBe(first)
    formSaved(had('⿴囗啚'), '器')
    const second = listForms('器')
    expect(second).not.toBe(first)
    await setForm(had('⿴囗啚'), '噐')
    expect(listForms('器')).not.toBe(second)
  })
})
