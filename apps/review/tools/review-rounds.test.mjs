import { test } from 'node:test'
import assert from 'node:assert/strict'
import { nextCharacter } from '../src/lib/reviewRounds.js'

const categories = [{ label: '仮', due: 1 }, { label: '假', due: 5 },
  { label: 'あ', due: 6 }, { label: 'い', due: 12 }, { label: 'う', due: 8 }]

test('a character with unseen flagged crops comes first, the one with most of them', () => {
  const flagged = [...categories, { label: 'え', due: 2, due_flagged: 1 }, { label: 'お', due: 3, due_flagged: 2 }]
  for (let seed = 0; seed < 20; seed++) assert.equal(nextCharacter(flagged, 'あ', [{ reading: 'お' }], seed), 'お')
  assert.equal(nextCharacter(flagged, 'お', [], 0), 'え')
})

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
  assert.equal(nextCharacter([{ label: '仮', due: 1 }], '仮', [], 0), null)
})
