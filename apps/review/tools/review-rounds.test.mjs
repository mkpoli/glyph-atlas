import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  dealtRound, firstCrops, nextGrapheme, mergeReferences, roundGraphemes, roundAddress,
  QUIZ_TILE_SIZES, QUIZ_CARDS_SIZE, QUIZ_COMPACT_SIZE, QUIZ_CARDS_MIN,
  normalizeQuizTileSize, switchQuizView, resizeQuizTiles, cropSectionForm, groupRoundByForm,
} from '../src/lib/reviewRounds.js'

const categories = [{ label: '仮', pending: 1 }, { label: '假', pending: 5 },
  { label: 'あ', pending: 6 }, { label: 'い', pending: 12 }, { label: 'う', pending: 8 }]

test('automatic rounds prefer a different unvisited full round', () => {
  for (let seed = 0; seed < 100; seed++) {
    assert.equal(nextGrapheme(categories, 'あ', [{ grapheme: 'い' }], seed), 'う')
  }
})

test('smaller characters follow the full rounds, largest first, before anything repeats', () => {
  const seen = [{ grapheme: 'あ' }, { grapheme: 'い' }, { grapheme: 'う' }]
  assert.equal(nextGrapheme(categories, 'う', seen, 0), '假')
  assert.equal(nextGrapheme(categories, '假', [...seen, { grapheme: '假' }], 0), '仮')
  assert.equal(nextGrapheme(categories.slice(0, 2), '', [], 0), '假')
})

test('once every character is visited, full rounds repeat', () => {
  const seen = categories.map(c => ({ grapheme: c.label }))
  for (let seed = 0; seed < 20; seed++) {
    assert.ok(['い', 'う'].includes(nextGrapheme(categories, 'あ', seen, seed)))
  }
  assert.equal(nextGrapheme([{ label: '仮', pending: 1 }], '仮', [], 0), null)
})

test('characters of one grapheme make one round, counting all their pending crops', () => {
  const rounds = roundGraphemes([{ label: '仮', grapheme: 'U+4EEE', pending: 2 }, { label: 'あ', grapheme: 'U+3042', pending: 3 },
    { label: '假', grapheme: 'U+4EEE', pending: 4 }, { label: '※', pending: 1 }])
  assert.deepEqual(rounds.map(r => [r.label, r.char, r.pending, r.members.map(m => m.label)]), [
    ['U+4EEE', '仮', 6, ['假', '仮']], ['U+3042', 'あ', 3, ['あ']], ['U+203B', '※', 1, ['※']]])
})

test('a named round is dealt whatever the counts hold, and gives way when it deals none', async () => {
  // The counts still file the seal character under itself, and hold no pending crop of 萅 or 春.
  const stale = roundGraphemes([{ label: '\u{3D307}', grapheme: 'U+3D307', pending: 3 }, { label: 'は', grapheme: 'U+306F', pending: 9 }])
  let asked
  const deal = (held, named, counts = stale) => { asked = []; return dealtRound(counts, named, left => left[0]?.label ?? null,
    async key => { asked.push(key); return { items: held.includes(key) ? [{ id: key }] : [] } }) }
  for (const counts of [stale, []]) {
    assert.equal((await deal(['U+6625', 'U+306F'], 'U+6625', counts)).chosen, 'U+6625')
    assert.deepEqual(asked, ['U+6625'])
  }
  // The same round again, in another material or after a refusal: the counts hold it no more than they did.
  assert.deepEqual(await deal(['U+306F'], 'U+6625'), { chosen: 'U+306F', result: { items: [{ id: 'U+306F' }] } })
  assert.deepEqual(asked, ['U+6625', 'U+306F'])
  assert.equal((await deal(['U+3D307'], null)).chosen, 'U+3D307')
  assert.deepEqual(asked, ['U+306F', 'U+3D307'])
  assert.deepEqual(await deal([], 'U+6625'), { chosen: null, result: null })
  assert.deepEqual(asked, ['U+6625', 'U+306F', 'U+3D307'])
  assert.equal((await dealtRound(stale, 'U+6625', () => null, async () => ({ items: [{}] }), () => true)).chosen, null)
})

test('the reference strip tags confirmed crops before seen ones', () => {
  const checked = [{ id: 'c1' }, { id: 'c2' }]
  const seen = [{ id: 's1' }, { id: 's2' }]
  assert.deepEqual(mergeReferences(checked, seen), [
    { id: 'c1', referenceState: 'checked' }, { id: 'c2', referenceState: 'checked' },
    { id: 's1', referenceState: 'seen' }, { id: 's2', referenceState: 'seen' },
  ])
})

test('a crop that is both checked and seen counts only once, as confirmed', () => {
  const checked = [{ id: 'c1' }]
  const seen = [{ id: 'c1' }, { id: 's1' }]
  assert.deepEqual(mergeReferences(checked, seen), [
    { id: 'c1', referenceState: 'checked' }, { id: 's1', referenceState: 'seen' },
  ])
})

test('each list is capped at the limit independently', () => {
  const checked = Array.from({ length: 20 }, (_, i) => ({ id: `c${i}` }))
  const seen = Array.from({ length: 20 }, (_, i) => ({ id: `s${i}` }))
  const result = mergeReferences(checked, seen, 3)
  assert.equal(result.filter(item => item.referenceState === 'checked').length, 3)
  assert.equal(result.filter(item => item.referenceState === 'seen').length, 3)
})

test('a round is addressed by its grapheme as the site writes one, and its material', () => {
  assert.equal(roundAddress('U+85CF', 'not:printed/type'), '/review/U+85CF?production=not:printed/type')
  assert.equal(roundAddress('U+304B U+309A', 'handwritten'), '/review/U+304B-U+309A?production=handwritten')
  assert.equal(roundAddress('', 'inscribed'), '/review?production=inscribed')
  assert.equal(roundAddress('U+3042'), '/review/U+3042')
  assert.equal(roundAddress('U+3042', '', true), '/review/U+3042?suspects=only')
  assert.equal(roundAddress('U+3042', 'handwritten', true), '/review/U+3042?production=handwritten&suspects=only')
})

test('a first batch of only kept crops reads on, and a grapheme with nothing past them gives way', async () => {
  const crops = Array.from({ length: 5 }, (_, i) => ({ id: 'p' + i }))
  let reads
  // A cursor here is the index of the next crop; the last page names none.
  const page = async cursor => { const at = cursor ?? 0; reads.push(at); const next = at + 2 < crops.length ? at + 2 : null
    return { items: crops.slice(at, at + 2), next, has_more: next !== null } }
  reads = []
  const round = await firstCrops(page, new Set(['p0', 'p1']))
  assert.deepEqual(reads, [0, 2])
  assert.deepEqual(round.items, [{ id: 'p2' }, { id: 'p3' }])
  assert.equal(round.next, 4)
  reads = []
  assert.deepEqual((await firstCrops(page, new Set(crops.map(c => c.id)))).items, [])
  assert.deepEqual(reads, [0, 2, 4])
  reads = []
  assert.deepEqual((await firstCrops(page, new Set())).items, [{ id: 'p0' }, { id: 'p1' }])
  assert.deepEqual(reads, [0])
})

test('switching Quick Review view mode restores each mode\'s tile size cleanly', () => {
  assert.deepEqual(switchQuizView('compact'), { view: 'compact', size: QUIZ_COMPACT_SIZE })
  assert.deepEqual(switchQuizView('cards'), { view: 'cards', size: QUIZ_CARDS_SIZE })
  assert.deepEqual(switchQuizView('compact', { compactSize: 60 }), { view: 'compact', size: 60 })
  assert.deepEqual(switchQuizView('cards', { cardsSize: 200 }), { view: 'cards', size: 200 })
  // A too-narrow saved cards size clamps back to default cards width.
  assert.deepEqual(switchQuizView('cards', { cardsSize: 76 }), { view: 'cards', size: QUIZ_CARDS_SIZE })
  assert.equal(normalizeQuizTileSize('not-a-number', 76), 76)
  assert.equal(normalizeQuizTileSize(999, 160), 160)
  assert.ok(QUIZ_TILE_SIZES.includes(QUIZ_CARDS_MIN))
})

test('resizing tiles below card width switches into compact mode, and keeps compact mode when growing', () => {
  assert.deepEqual(resizeQuizTiles(76, 'cards'), { view: 'compact', size: 76 })
  assert.deepEqual(resizeQuizTiles(48, 'cards'), { view: 'compact', size: 48 })
  assert.deepEqual(resizeQuizTiles(124, 'cards'), { view: 'cards', size: 124 })
  assert.deepEqual(resizeQuizTiles(200, 'cards'), { view: 'cards', size: 200 })
  assert.deepEqual(resizeQuizTiles(124, 'compact'), { view: 'compact', size: 124 })
})

test('groupRoundByForm partitions crops by written form in member order while preserving in-section order', () => {
  const crops = [
    { id: 'c1', label: '假', shape_order: 1 },
    { id: 'c2', label: '仮', shape_order: 2 },
    { id: 'c3', label: '假', shape_order: 3 },
    { id: 'c4', label: '𛂞', jibo: '八', shape_order: 4 },
    { id: 'c5', label: '仮', shape_order: 5 },
  ]
  const sections = groupRoundByForm(crops, ['仮', '假'], '仮')
  assert.deepEqual(sections.map(s => [s.form, s.jibo, s.items.map(i => i.id)]), [
    ['仮', null, ['c2', 'c5']],
    ['假', null, ['c1', 'c3']],
    ['𛂞', '八', ['c4']],
  ])
})

test('cropSectionForm prefers frozen dealtForm, then standing claimed form, then written character', () => {
  assert.equal(cropSectionForm({ label: '仮', dealtForm: '假' }), '假')
  assert.equal(cropSectionForm({ label: '所', form: { status: 'asserted', values: [{ text: '⿰戶斤' }] } }), '⿰戶斤')
  assert.equal(cropSectionForm({ label: '所', form: { status: 'disputed', values: [{ text: '⿰戶斤' }] } }), '所')
  assert.equal(cropSectionForm({ written_character: '嶋', label: '島' }), '嶋')
  assert.equal(cropSectionForm({}, 'あ'), 'あ')
})
