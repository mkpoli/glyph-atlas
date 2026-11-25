import { expect, test } from 'bun:test'
import { checkClaims, type CropNow, PREDICATES, RESOLVER, resolveWriteQuery, restsOn, type Member } from './ledger'

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

test('the resolver names its version and reads crop evidence on the crop as it stands', () => {
  expect(resolveWriteQuery()).toContain(`'${RESOLVER}' AS resolver`)
  expect(resolveWriteQuery()).toContain('AND NOT EXISTS (SELECT 1 FROM (SELECT c.cut,c.kind,rv.crop AS waits,n.pixels,')
  expect(resolveWriteQuery()).not.toContain('crop_now')
  expect(resolveWriteQuery()).not.toMatch(/^\s*--/m)
})

test('a claim rests on the box its evidence names, by its cut or its pixels', () => {
  const crop = (fields: Partial<CropNow> = {}): CropNow => ({ id: 1, cut: 1, kind: 0, text: 'ex:1', version: 'c1@px@0,0,9,9',
    published: { cut: 1, pixels: '@px', box: '@0,0,9,9' }, ...fields })
  const waiting = crop({ cut: 2, version: 'c1@px@1,0,9,9' })
  for (const [held, at, cut, version] of [
    [true, crop(), 1, 'ex:1@px@0,0,9,9'], [true, crop(), 0, 'ex:1@px@0,0,9,9'], [true, crop(), 1, 'ex:1@other@0,0,9,9'],
    [false, crop(), 0, 'ex:1@other@0,0,9,9'], [false, crop(), 1, 'c1@px@1,0,9,9'], [false, crop(), 1, null],
    [true, waiting, 2, 'c1@px@1,0,9,9'], [false, waiting, 2, 'c1@px@2,0,9,9'], [false, waiting, 1, 'c1@px@0,0,9,9'], [false, waiting, 1, 'c1@other@1,0,9,9'],
    [true, crop({ kind: 1 }), 1, 'c1@older@5,5,5,5'], [false, crop({ kind: 1 }), 0, 'c1@older@0,0,9,9'],
  ] as const) expect([at.cut, cut, version, restsOn(at, cut, version)]).toEqual([at.cut, cut, version, held])
})
