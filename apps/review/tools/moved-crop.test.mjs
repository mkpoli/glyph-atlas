import { describe, expect, it } from 'bun:test'
import { movedQuery } from '../src/lib/movedCrop.js'
import { showing } from '../src/lib/inspectQueue.js'

const record = { id: '3dh00000', source_id: 'hk:1:a' }

describe('an old crop address', () => {
  it('keeps the cited version for the crop\'s own old id or another spelling of its public id', () => {
    expect(movedQuery(record, 'hk:1:a', '?v=abc123&x=1')).toBe('?v=abc123&x=1')
    expect(movedQuery(record, '3DH00000', '?v=abc123')).toBe('?v=abc123')
  })
  it('drops it for a retired crop, whose versions are not its replacement\'s', () => {
    expect(movedQuery(record, 'hk:1:old', '?v=abc123&x=1')).toBe('?x=1')
    expect(movedQuery(record, '3dh00001', '?v=abc123')).toBe('')
  })
})

describe('the crop the inspector shows', () => {
  it('is found in its list after an address swap by the id the list asked for or its source id', () => {
    const swapped = { id: '3dh00000', was: 'hk:1:a', source: 'hk:1:a' }
    expect(showing({ id: 'hk:1:a' }, swapped)).toBe(true)
    expect(showing({ id: '3dh00000' }, swapped)).toBe(true)
    expect(showing({ id: '3dh00009', source_id: 'hk:1:a' }, { id: '3dh00000', source: 'hk:1:a' })).toBe(true)
    expect(showing({ id: '3dh00001', source_id: 'hk:1:b' }, swapped)).toBe(false)
    expect(showing({ id: 'x' }, null)).toBe(false)
  })
})
