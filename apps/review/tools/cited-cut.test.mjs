import { describe, expect, it } from 'bun:test'
import { citedCut } from '../src/lib/citedCut.js'
import { versionToken } from '../../cloudflare/src/citation.ts'

const older = `c:1@${'b'.repeat(64)}@1,2,3,4`
const record = { id: 'c:1', crop_version: `c:1@${'a'.repeat(64)}@5,6,7,8` }
const now = versionToken('c:1', record.crop_version), before = versionToken('c:1', older)
const versions = (status, body) => async () => new Response(JSON.stringify(body), { status })

describe('the cut an address cites', () => {
  it('is the crop’s own when the token names its cut now, read without a request', async () => {
    const fetch = () => { throw new Error('no request expected') }
    expect(await citedCut(record, now, fetch)).toEqual({ id: 'c:1', token: now, current: true, version: null })
  })
  it('is an earlier recorded cut, with when it was recorded', async () => {
    const row = { id: older, image: '/atlas/media/old.webp', at: '2026-09-01T00:00:00.000Z' }
    expect(await citedCut(record, before, versions(200, { versions: [row] }))).toEqual({ id: 'c:1', token: before, current: false, version: row })
  })
  it('names no cut when the crop has none by that name, and says nothing when the versions cannot be read', async () => {
    expect((await citedCut(record, 'zzzzzz', versions(200, { versions: [] }))).version).toBeNull()
    expect(await citedCut(record, 'zzzzzz', versions(503, {}))).toBeNull()
  })
})
