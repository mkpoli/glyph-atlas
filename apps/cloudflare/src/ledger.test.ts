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

test('the resolver names its version and runs over the site\'s own crops', () => {
  expect(resolveWriteQuery()).toContain(`'${RESOLVER}' AS resolver`)
  expect(resolveWriteQuery()).toContain('SELECT id,crop_version FROM units')
  expect(resolveWriteQuery()).not.toMatch(/^\s*--/m)
})
