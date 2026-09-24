import { test } from 'node:test'
import assert from 'node:assert/strict'
import { nextCharacter } from '../src/lib/reviewRounds.js'

test('automatic rounds skip singletons and prefer a different unvisited character', () => {
  const categories = [{ label: '仮', pending: 1 }, { label: '假', pending: 5 },
    { label: 'あ', pending: 6 }, { label: 'い', pending: 12 }, { label: 'う', pending: 8 }]
  for (let seed = 0; seed < 100; seed++) {
    assert.equal(nextCharacter(categories, 'あ', [{ reading: 'い' }], seed), 'う')
  }
  assert.equal(nextCharacter(categories.slice(0, 2), '', [], 0), null)
  assert.equal(nextCharacter(categories, 'う', [{ reading: 'あ' }, { reading: 'い' }], 0), 'あ')
})
