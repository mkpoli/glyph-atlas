import { expect, test } from 'bun:test'
import { categoryOf } from './category'
import { FORM_TEXT_LONGEST, anchoredForm, compatibility, drawnForm, drawnFormsOf, formProblem, standsFor, isSequence, representationId, structureKey, typed, written, type Representation } from './representation'

test('a character or a description is a form a reviewer may type', () => {
  for (const value of ['𮟃', '還', 'が', '葛\u{E0100}', '⿺辶𦊷', '⿰氵⿱冖月', '⿲木木木', '⿱⿰口口⿰口口', '〾⿰木？', '⿰木', '⿰⺡骨'])
    expect(formProblem(value)).toBeNull()
  expect(isSequence('⿺辶𦊷')).toBe(true)
  expect(isSequence('𮟃')).toBe(false)
})

test('anything else names what is wrong with it', () => {
  expect(['', ' 還', '還還', '\u3099', 'x'.repeat(65), '⿰木\u200b', '⿰木 木', '🇯🇵', '👍🏽', '\u1100\u1161'].map(formProblem)).toEqual(Array(10).fill('character'))
  expect(['⿰木', '⿰', '⿲木木'].map(formProblem)).toEqual(['missing', 'missing', 'missing'])
  expect(['⿰木木木', '⿰木木︀木'].map(formProblem)).toEqual(['extra', 'extra'])
  // The editor's empty-slot mark, and the one drafts once used, are never saved.
  expect(['⿰木⬚', '⿰木＋'].map(formProblem)).toEqual(['component', 'component'])
  expect(['⿰木a', '⿰木あ', '⿰木ー'].map(formProblem)).toEqual(['component', 'component', 'component'])
  // A katakana letter stands for a component of its shape, as manuscripts write 疑 with コ.
  expect(['⿰⿱匕失⿱コ疋', '⿰木ア'].map(formProblem)).toEqual([null, null])
})

test('what is typed takes the scheme it is written in', () => {
  expect(typed('𮟃')).toEqual({ scheme: 'unicode', value: '𮟃', namespace: null, version: null })
  expect(typed('⿺辶𦊷')).toEqual({ scheme: 'ids', value: '⿺辶𦊷', namespace: null, version: null })
  expect(typed('葛\u{E0100}')).toEqual({ scheme: 'ivs', value: '葛\u{E0100}', namespace: null, version: null })
  expect(typed('\uE000')).toBe('private')
  expect(typed('⿰木')).toBe('missing')
})

test('ids are what the review service derives for the same names', async () => {
  // tests/test_representation.py holds the same values.
  const vectors: [string, string, string][] = [['𮟃', 'rp:b78a8585df2e755ac741ca9c16c933f4', 'fm:9a7e168e47aef52e2958cff4ba7cf858'],
    ['⿺辶𦊷', 'rp:4fd4eff3084f8bbbe5489e119eea2862', 'fm:9c8fb02868fdcbc3357a0b63ab17f5f4'],
    ['葛\u{E0100}', 'rp:e3640385ca612cd97bec3b1e527fd705', 'fm:8b7650bbf9099669cfa0f0132a3bd18a']]
  for (const [value, rep, form] of vectors) {
    const id = await representationId(typed(value) as Representation)
    expect([id, await anchoredForm(id)]).toEqual([rep, form])
  }
  expect(await representationId({ scheme: 'pua', value: '\uE000', namespace: 'example', version: '1' })).toBe('rp:24234b052e1ad13df2cca72c95de5246')
})

test('a drawn form is a private-use character of the site\'s own table', async () => {
  // tests/test_drawn_forms.py holds the same values.
  const form = typed('\u{F0000}') as Representation
  expect(form).toEqual({ scheme: 'pua', value: '\u{F0000}', namespace: 'glyph-atlas', version: '1' })
  const id = await representationId(form)
  expect([id, await anchoredForm(id)]).toEqual(['rp:2f0d058ce9a2c8ddbfd1a3d61533d290', 'fm:9b7ed9e8487cec8865fe33b08841f13b'])
  expect(drawnForm('\u{F0000}')).toMatchObject({ for: '事', script: 'han' })
  expect(drawnForm('\u{F0000}')!.name).toContain('事')
  // It is filed and counted as the grapheme it writes is.
  expect([categoryOf('\u{F0000}'), categoryOf('事'), categoryOf('\u{F0100}')]).toEqual(['kanji', 'kanji', 'other'])
  expect(drawnFormsOf('事')).toEqual(['\u{F0000}', '\u{F0001}', '\u{F0002}'])
  expect(drawnFormsOf('也')).toEqual([])
  // A code point the table does not give names nothing, in the same area or another.
  expect(typed('\u{F0100}')).toBe('private')
  expect(drawnForm('\u{F0100}')).toBeUndefined()
  expect(drawnForm('toString')).toBeUndefined()
})

test('a text names its characters and descriptions one after another', () => {
  expect(written('說⿰月兊説')).toEqual(['說', '⿰月兊', '説'])
  expect(written('⿰氵⿵戊天⿱𥫗冊')).toEqual(['⿰氵⿵戊天', '⿱𥫗冊'])
  expect(written('葛\u{E0100}峰')).toEqual(['葛\u{E0100}', '峰'])
  // What follows a description cut short, or one with a part no description may name, is left out.
  expect(written('峰⿰山')).toEqual(['峰'])
  expect(written('⿰山a峰')).toEqual([])
  expect(written('')).toEqual([])
  expect(written('峰' + '〾'.repeat(2000) + '山峯')).toEqual(['峰'])
})

test('a structure is kept under the description as written and the character without its selector', () => {
  expect(structureKey('⿰月兊')).toBe('⿰月兊')
  expect(structureKey('葛\u{E0100}')).toBe('葛')
  expect(structureKey('\uf904')).toBe('滑')
})

test('a compatibility ideograph stands for a unified one, and the unified ideographs of its block do not', () => {
  // 說 U+F96F and U+F9A1 stand for 說 U+8AAA, 突 U+FA55 for 突 U+7A81, 杓 U+2F8DC for 杓 U+6753.
  expect(['\uf96f', '\uf9a1', '\ufa55', '\u{2f8dc}'].map(compatibility)).toEqual([true, true, true, true])
  // 﨑 U+FA11 and 﨟 U+FA1F are unified ideographs; 說 with a selector is a sequence, not a code point.
  expect(['說', '説', '\ufa11', '\ufa1f', '說\ufe00', '⿰言兌', 'が', ''].map(compatibility)).toEqual(Array(8).fill(false))
})

test('a typed compatibility ideograph is the figure it was encoded for, or its unified ideograph', () => {
  // 突 U+FA55 implements MJ030283, 突 U+7A81 U+E0103; 說 U+F96F has no registered figure.
  expect(standsFor('\ufa55')).toBe('\u7a81\u{e0103}')
  expect(standsFor('\uf96f')).toBe('\u8aaa')
  expect(['說', '\ufa11', '⿰言兌', '說\u{e0101}'].map(standsFor)).toEqual(['說', '\ufa11', '⿰言兌', '說\u{e0101}'])
  expect(typed('\ufa55')).toEqual({ scheme: 'ivs', value: '\u7a81\u{e0103}', namespace: null, version: null })
  expect(typed('\uf96f')).toEqual({ scheme: 'unicode', value: '\u8aaa', namespace: null, version: null })
  // As the base of a sequence or a part of a description it is its unified ideograph.
  expect(typed('\ufa55\u{e0103}')).toEqual({ scheme: 'ivs', value: '\u7a81\u{e0103}', namespace: null, version: null })
  expect(typed('⿰\ufa55木')).toEqual({ scheme: 'ids', value: '⿰\u7a81木', namespace: null, version: null })
})

test('a form holding a part no code point names is typed in its one spelling', () => {
  const one = { scheme: 'ids', value: '⿱&X-00087;龴', namespace: null, version: null };
  for (const value of ['⿱{88}龴', '⿱⿵𠆢一龴', '⿱&X-00087;龴']) expect(typed(value)).toEqual(one);
  expect(written('⿱&X-00087;龴令')).toEqual(['⿱&X-00087;龴', '令']);
  expect(formProblem('⿰'.repeat(15) + '&X-00087;'.repeat(16))).toBeNull();
  expect(typed('⿱{999}龴')).toBe('component');
  expect(['⿱&X-01653;龴', '⿱&X-99999;龴', '⿱&CDP-8BD0;龴'].map(formProblem)).toEqual(['component', 'component', 'component']);
  expect(typed('⿱&X-01653;一')).toEqual({ ...one, value: '⿱&X-00026;一' });
});

test('a part takes no variation selector, and a long form fits a request', () => {
  expect(typed('⿱&X-00087;︀木')).toBe('component');
  expect(typed('⿱⿵𠆢一木︀')).toEqual({ scheme: 'ids', value: '⿱&X-00087;木︀', namespace: null, version: null });
  const longest = '⿰'.repeat(31) + '&X-00087;'.repeat(32);
  expect(formProblem(longest)).toBeNull();
  expect(longest.length).toBeLessThanOrEqual(FORM_TEXT_LONGEST);
});
