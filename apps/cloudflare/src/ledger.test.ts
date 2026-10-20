import { expect, test } from 'bun:test'
import { checkClaims, PREDICATES, RESOLVER, resolveWriteQuery, type Member } from './ledger'

const fail = (status: number, message: string): never => { throw new Error(`${status} ${message}`) }
const member = (fields: Partial<Member>): Member => ({ object: null, value: null, confidence: null, confidence_scheme: null, ...fields })

test('the catalogue takes what a predicate allows', () => {
  expect(PREDICATES.has_form.subject).toBe('crop')
  checkClaims('has_form', [member({ value: 'unresolved' })], {}, fail)
  checkClaims('has_form', [member({ value: 'unresolved', confidence: 0.6, confidence_scheme: 'reviewer-weight' }),
    member({ value: 'unreadable', confidence: 0.4, confidence_scheme: 'reviewer-weight' })], {}, fail)
})

test('and refuses the rest, as the review service does', () => {
  const refused = (predicate: string, members: Member[]) => { try { checkClaims(predicate, members, {}, fail); return null } catch (e) { return String(e) } }
  expect(refused('reads_as', [member({ value: 'x' })])).toContain('422 Unknown predicate')
  expect(refused('has_form', [])).toContain('422')
  expect(refused('has_form', [member({ value: 'legible' })])).toContain('is not a value')
  expect(refused('has_form', [member({ object: 'fm:x' })])).toContain('cannot be the object')
  expect(refused('has_form', [member({ value: 'unresolved', confidence: 0.5 })])).toContain('names its scheme')
  expect(refused('has_form', [member({ value: 'unresolved' }), member({ value: 'unresolved' })])).toContain('differ')
})

test('an attestation names a word or a gloss, how the sense is known, and how sure the form is', () => {
  const refused = (members: Member[], method: string | null = 'ruby', kinds: Record<string, string> = { 'ja/にしん/名詞': 'word' }) => {
    try { checkClaims('writes_word', members, kinds, fail, method); return null } catch (e) { return String(e) } }
  const word = member({ object: 'ja/にしん/名詞', confidence: 0.5, confidence_scheme: 'form' })
  expect(refused([word])).toBeNull()
  expect(refused([member({ value: { gloss: 'herring' }, confidence: 1, confidence_scheme: 'form' })], 'context')).toBeNull()
  expect(refused([word], null)).toContain('names how it is known')
  expect(refused([word], 'guess')).toContain('names how it is known')
  expect(refused([member({ object: 'ja/にしん/名詞' })])).toContain('confidence at one of these levels')
  expect(refused([member({ object: 'ja/にしん/名詞', confidence: 0.7, confidence_scheme: 'form' })])).toContain('levels')
  expect(refused([member({ value: 'disputed', confidence: 1, confidence_scheme: 'form' })])).toContain('is not a value')
  expect(refused([member({ value: { gloss: ' ' }, confidence: 1, confidence_scheme: 'form' })])).toContain('is not a value')
  expect(refused([word], 'ruby', { 'ja/にしん/名詞': 'form' })).toContain('cannot be the object')
  const evidence = (items: { kind: string; ref: string; locator: string | null }[]) => {
    try { checkClaims('writes_word', [word], { 'ja/にしん/名詞': 'word' }, fail, 'ruby', items); return null } catch (e) { return String(e) } }
  expect(evidence([{ kind: 'source', ref: 'rubies:l1:ruby0:right', locator: null }])).toBeNull()
  expect(evidence([{ kind: 'crop', ref: 'x', locator: null }])).toContain('Evidence is')
  expect(evidence([{ kind: 'page', ref: 'p1', locator: 'a' }, { kind: 'page', ref: 'p1', locator: 'b' }])).toContain('named once')
  expect(refused([member({ value: { gloss: '𠀀'.repeat(200) }, confidence: 1, confidence_scheme: 'form' })], 'gloss')).toBeNull()
  expect(refused([member({ value: { gloss: '𠀀'.repeat(201) }, confidence: 1, confidence_scheme: 'form' })], 'gloss')).toContain('is not a value')
  expect(refused([member({ value: { gloss: 'herring ' }, confidence: 1, confidence_scheme: 'form' })], 'gloss')).toContain('is not a value')
})

test('the resolver names its version and compares crop evidence by crop and cut', () => {
  expect(resolveWriteQuery()).toContain(`'${RESOLVER}' AS resolver`)
  expect(resolveWriteQuery()).toContain('e.cut IS NOT (SELECT c.cut FROM crops c WHERE c.id=a.crop)')
  expect(resolveWriteQuery()).not.toContain('crop_now')
  expect(resolveWriteQuery()).not.toMatch(/^\s*--/m)
})
