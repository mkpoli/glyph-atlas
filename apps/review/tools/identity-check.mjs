import assert from 'node:assert/strict'
import { isUnassigned, writtenLabel, visualGroup, matchesVisualGroup, scriptInfo, scriptParts } from '../src/lib/identity.js'
const unresolved = { char: '仮', label: '仮', source_label: '仮', written_character: null,
  identity_status: 'unassigned', identity_basis: 'normalized_transcription', visual_group: { id: 'shape:3', label: 'Group 3' } }
assert.equal(writtenLabel(unresolved), 'Unassigned')
assert.equal(visualGroup(unresolved).label, 'Group 3')
assert.equal(visualGroup({ ...unresolved, visual_group: { ...unresolved.visual_group, written_character: '假' } }).label, 'Group 3')
assert.equal(matchesVisualGroup(unresolved, 'unassigned'), true)
assert.equal(matchesVisualGroup(unresolved, 'shape:3'), true)
assert.equal(matchesVisualGroup(unresolved, 'shape:4'), false)
const assigned = { ...unresolved, written_character: '假', identity_status: 'assigned', identity_basis: 'human_review' }
assert.equal(isUnassigned(assigned), false)
assert.equal(writtenLabel(assigned), '假')
assert.equal(matchesVisualGroup(assigned, 'unassigned'), false)
for (const [text, key] of [['ム', 'katakana'], ['厶', 'kanji'], ['あ', 'hiragana'], ['𛀂', 'hiragana'], ['𪜈', 'katakana'], ['𛄧', 'katakana'], ['𛄣', 'hiragana'], ['↔', 'symbol'], ['が', 'hiragana'], ['假ム', 'mixed'], ['ー', 'katakana'], ['カー', 'katakana']]) {
  assert.equal(scriptInfo(text).key, key, text)
}
assert.deepEqual(scriptInfo('𪜈', 'katakana'), { key: 'katakana', label: 'Katakana' })
assert.deepEqual(scriptInfo('ー', 'symbol'), { key: 'katakana', label: 'Katakana' })
assert.equal(encodeURIComponent('𪜈'), '%F0%AA%9C%88')
assert.deepEqual(scriptParts('假ムが※').map(part => [part.text, part.key]),
  [['假', 'kanji'], ['ム', 'katakana'], ['が', 'hiragana'], ['※', 'symbol']])
assert.equal(scriptParts('𪜈', 'katakana').length, 1)
console.log('Identity display and script distinctions passed.')
