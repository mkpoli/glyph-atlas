import { test } from 'node:test'
import assert from 'node:assert/strict'
import { nextCharacter } from '../src/lib/reviewRounds.js'

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
