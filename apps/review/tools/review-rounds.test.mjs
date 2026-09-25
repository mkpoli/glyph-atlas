import { test } from 'node:test'
import assert from 'node:assert/strict'
import { nextCharacter, mergeReferences } from '../src/lib/reviewRounds.js'

const categories = [{ label: '仮', pending: 1 }, { label: '假', pending: 5 },
  { label: 'あ', pending: 6 }, { label: 'い', pending: 12 }, { label: 'う', pending: 8 }]

test('automatic rounds prefer a different unvisited full round', () => {
  for (let seed = 0; seed < 100; seed++) {
    assert.equal(nextCharacter(categories, 'あ', [{ reading: 'い' }], seed), 'う')
  }
})

test('smaller characters follow the full rounds, largest first, before anything repeats', () => {
  const seen = [{ reading: 'あ' }, { reading: 'い' }, { reading: 'う' }]
  assert.equal(nextCharacter(categories, 'う', seen, 0), '假')
  assert.equal(nextCharacter(categories, '假', [...seen, { reading: '假' }], 0), '仮')
  assert.equal(nextCharacter(categories.slice(0, 2), '', [], 0), '假')
})

test('once every character is visited, full rounds repeat', () => {
  const seen = categories.map(c => ({ reading: c.label }))
  for (let seed = 0; seed < 20; seed++) {
    assert.ok(['い', 'う'].includes(nextCharacter(categories, 'あ', seen, seed)))
  }
  assert.equal(nextCharacter([{ label: '仮', pending: 1 }], '仮', [], 0), null)
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
