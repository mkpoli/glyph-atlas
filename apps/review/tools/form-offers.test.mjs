import { describe, expect, it } from 'bun:test'
import { formsOf, groupForms, keyOrder, notoDraws, sortForms } from '../src/lib/formOffers.js'

// 邦's Moji_Joho figures as the MJ figure list gives them, with their IVD descriptions; MJ026242 is
// the default.
const bangFigures = [['\u{E0103}', 'MJ026242', true, '⿰丰阝'], ['\u{E0104}', 'MJ026246', false, '⿰龵阝'], ['\u{E0105}', 'MJ026244', false, '⿰丰阝']]
  .map(([selector, mj, isDefault, ids]) => ({ char: '邦' + selector, mj, default: isDefault, ids }))
// 臭's: MJ021299 drawn ⿱自大, the default, and MJ030291 drawn ⿱自犬.
const chouFigures = [{ char: '臭\u{E0102}', mj: 'MJ021299', default: true, ids: '⿱自大' },
  { char: '臭\u{E0103}', mj: 'MJ030291', default: false, ids: '⿱自犬' }]
// Shapes as /layers/ids gives them.
const bangShapes = [{ text: '⿰丰阝', regions: 'GHTJKV' }, { text: '⿰⿻㇒𰀁阝', regions: 'P' }]
// 𦤀 encodes 臭's J shape again.
const chouShapes = [{ text: '⿱自犬', regions: 'GHTKPV' }, { text: '⿱自大', regions: 'J', chars: ['𦤀'] }]

describe('form offers', () => {
  it("offers a figure in place of the regional shape that is its description", () => {
    const sorted = sortForms('臭', chouFigures, chouShapes)
    expect(sorted.figures.map(figure => figure.mj)).toEqual(['MJ030291'])
    expect(sorted.alike.shapes.map(shape => shape.text)).toContain('⿱自犬')
  })

  it('sets apart 臭\'s J shape and 𦤀, which encodes it again: both draw as 臭', () => {
    const sorted = sortForms('臭', chouFigures, chouShapes)
    expect(sorted.shapes).toEqual([])
    expect(sorted.alike.shapes.map(shape => shape.text)).toEqual(['⿱自犬', '⿱自大'])
    expect(sorted.alike.chars).toEqual(['𦤀'])
  })

  it('keeps a shape only a set-aside figure describes', () => {
    const shapes = [{ text: '⿱自大', regions: 'TV' }]
    expect(sortForms('臭', chouFigures, shapes).shapes).toEqual(shapes)
  })

  it('offers the character a shape is in place of a shape outside J', () => {
    // 稽's K shape ⿰禾⿱尤㫖 is 𥡴, which the bar offers; 𥡴 is not drawn as 稽, so it stays.
    const sorted = sortForms('稽', [], [{ text: '⿰禾⿱尤旨', regions: 'GHTJPV' }, { text: '⿰禾⿱尤㫖', regions: 'K', chars: ['𥡴'] }])
    expect(sorted.shapes).toEqual([])
    expect(sorted.alike.chars).toEqual([])
  })

  it('offers a figure in place of its description for a character another font draws', () => {
    // 𠦄 U+20984: BabelStone gives ⿱十⿰𠂇十 for G and P and ⿱十卄 for V.
    const figures = [{ char: '𠦄\u{E0101}', mj: 'MJ031728', default: false, ids: '⿱十⿰𠂇十' },
      { char: '𠦄\u{E0102}', mj: 'MJ031729', default: true, ids: '⿱十⿰十十' }]
    const sorted = sortForms('𠦄', figures, [{ text: '⿱十⿰𠂇十', regions: 'GP' }, { text: '⿱十卄', regions: 'V' }])
    expect(sorted.figures).toEqual(figures)
    expect(sorted.shapes.map(shape => shape.text)).toEqual(['⿱十卄'])
  })

  it('sets apart the default figure and the Japanese shape of a character Noto CJK JP draws', () => {
    const sorted = sortForms('邦', bangFigures, bangShapes)
    expect(sorted.figures.map(figure => figure.mj)).toEqual(['MJ026246', 'MJ026244'])
    expect(sorted.shapes.map(shape => shape.text)).toEqual(['⿰⿻㇒𰀁阝'])
    expect(sorted.alike.figures.map(figure => figure.mj)).toEqual(['MJ026242'])
    expect(sorted.alike.shapes.map(shape => shape.text)).toEqual(['⿰丰阝'])
  })

  it('sets apart 臭\'s Japanese shape ⿱自大, a spelling of 臭\'s own 構形', () => {
    const sorted = sortForms('臭', [], [{ text: '⿱自犬', regions: 'GHTKPV' }, { text: '⿱自大', regions: 'J' }])
    expect(sorted.shapes.map(shape => shape.text)).toEqual(['⿱自犬'])
    expect(sorted.alike.shapes.map(shape => shape.text)).toEqual(['⿱自大'])
  })

  it('offers every form of a character drawn from another font', () => {
    // 𠔥 U+20525 is outside Noto CJK JP; Plangothic draws it unlike its default MJ figure.
    const figures = [{ char: '𠔥\u{E0100}', mj: 'MJ031010', default: false }, { char: '𠔥\u{E0108}', mj: 'MJ059329', default: true }]
    expect(sortForms('𠔥', figures, []).figures).toEqual(figures)
  })

  it('knows the ranges both faces take from Noto CJK JP', () => {
    expect(['㐀', '䶵', '一', '鿯'].map(notoDraws)).toEqual([true, true, true, true])
    expect(['䶶', '鿰', '𠀀', '邦\u{E0103}'].map(notoDraws)).toEqual([false, false, false, false])
  })
})

// 春's family and the 構形 of each member, as the site's reference data gives them: 𽌇 is 萅's small
// seal character and stands under its structure. The family has eleven Han forms.
const SHUN = '\u{3D307}'
const shun = [['春', '⿱𡗗日'], ['旾', '⿱屯日'], ['㫩', '⿱卉日'], ['萅', '⿱艸旾'], ['𣆊', '⿱𠀐日'], ['𣇸', '⿳卉𠆢日'], ['𣈤', '⿱芔日'],
  ['𣊨', '⿱芔𣅆'], ['𦸬', '⿳芚旦丷'], ['𧇶', '⿺虎春'], ['𰋠', '⿱𡗗屯'], [SHUN, '⿱艸旾']]
const standingOf = list => new Map(list.filter(([, label]) => label).map(([char, label]) => [char, { id: 'st:' + label, label }]))
const barOf = list => list.map(([char]) => ({ char }))
const charsOf = group => formsOf(group).map(form => form.char)

describe('a small seal character on the bar', () => {
  it('stands last in its 構形 and takes a number key after every Han form', () => {
    const groups = groupForms(barOf(shun), { structureOf: standingOf(shun), own: 'st:⿱𡗗日' })
    expect(charsOf(groups.find(group => group.label === '⿱艸旾'))).toEqual(['萅', SHUN])
    expect(groups.map(group => group.blocks[0].own)).toEqual([true, ...Array(10).fill(false)])
    const keys = keyOrder(groups).map(form => form.char)
    expect(keys).toEqual(shun.map(([char]) => char))
    expect(keys.slice(0, 10)).not.toContain(SHUN)
    // Without it, the keys are the same.
    expect(keyOrder(groupForms(barOf(shun.slice(0, -1)), { structureOf: standingOf(shun) })).map(form => form.char)).toEqual(keys.slice(0, 11))
  })
  it('follows the figures of its 構形 and the related characters, and stands alone where it has none', () => {
    // 臭's bar: the figure MJ030291 and the seal character under 臭's structure, 臰 under its own.
    const chou = [['臭', '⿱自犬'], ['臭\u{E0103}', '⿱自犬'], ['臰', '⿱自死'], ['\u{3EC9F}', '⿱自犬']]
    const bar = groupForms(barOf(chou), { structureOf: standingOf(chou), related: new Set(['臰']) })
    expect(bar.map(charsOf)).toEqual([['臭', '臭\u{E0103}', '\u{3EC9F}'], ['臰']])
    expect(keyOrder(bar).map(form => form.char)).toEqual(['臭', '臭\u{E0103}', '臰', '\u{3EC9F}'])
    // 折's three: 𽋍 under 㪿's structure, and two Unicode gives one value, which have none.
    const setsu = [['折', '⿰手斤'], ['㪿', '⿰⿱䶹䶹斤'], ['\u{3D2CD}', '⿰⿱䶹䶹斤'], ['\u{3D2CE}', ''], ['\u{3D2CF}', '']]
    expect(groupForms(barOf(setsu), { structureOf: standingOf(setsu) }).map(group => [group.label, charsOf(group)])).toEqual([
      ['⿰手斤', ['折']], ['⿰⿱䶹䶹斤', ['㪿', '\u{3D2CD}']], ['', ['\u{3D2CE}', '\u{3D2CF}']]])
  })
  it('stands in a block of its own, the last of its 構形, where it is built as a layout of it', () => {
    // 遇's family: 𽓻 is proposed ⿰辵禺, which stands under ⿺辶禺.
    const GU = '\u{3D4FB}', whole = { id: 'st:⿺辶禺', label: '⿺辶禺' }, layout = { id: 'st:⿰辵禺', label: '⿰辵禺' }
    const gu = [['遇', '⿺辶禺'], [GU, ''], ['𨔆', '⿺辶於'], ['𫑈', '⿺辶寓']]
    const structureOf = standingOf(gu).set(GU, { ...whole, by: null, part: layout })
    const groups = groupForms(barOf(gu), { structureOf, own: layout.id, proposed: (char, by) => char === GU && !by })
    expect(groups.map(group => [group.label, group.blocks.map(block => [block.label, block.own, block.forms.map(form => [form.char, form.proposed])])])).toEqual([
      ['⿺辶禺', [['⿺辶禺', false, [['遇', false]]], ['⿰辵禺', true, [[GU, true]]]]],
      ['⿺辶於', [['⿺辶於', false, [['𨔆', false]]]]], ['⿺辶寓', [['⿺辶寓', false, [['𫑈', false]]]]]])
    expect(keyOrder(groups).map(form => form.char)).toEqual(['遇', '𨔆', '𫑈', GU])
  })
  it('leaves a bar without one as it stood', () => {
    const bang = [['邦', '⿰丰阝'], ['㘯', ''], ['𨛜', '⿰𭅺阝'], ['邽', '⿰圭阝']]
    const groups = groupForms(barOf(bang), { structureOf: standingOf(bang), related: new Set(['邽']) })
    expect(groups.map(group => [group.id, charsOf(group)])).toEqual([
      ['st:⿰丰阝', ['邦']], ['st:⿰𭅺阝', ['𨛜']], ['', ['㘯']], ['st:⿰圭阝', ['邽']]])
    expect(keyOrder(groups)).toEqual(groups.flatMap(formsOf))
  })
})
