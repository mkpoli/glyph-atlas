import { describe, expect, it } from 'bun:test'
import { cropOrigin, movedQuery, redirectPath } from '../src/lib/movedCrop.js'
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

describe('crop origin routing', () => {
  const localize = (path, lang) => `/${lang}${path}`
  it('normalizes item origins cleanly', () => {
    expect(cropOrigin({ origin: 'corpus' })).toBe('corpus')
    expect(cropOrigin({ origin: 'local' })).toBe('collection')
    expect(cropOrigin({})).toBe('collection')
    expect(cropOrigin(null)).toBe('collection')
  })
  it('redirects corpus glyphs opened under /crop/ to /corpus/, keeping citations', () => {
    const corpus = { id: '5se0004h', source_id: 'codh:200014685:g', origin: 'corpus' }
    expect(redirectPath(corpus, 'collection', { lang: 'ja', id: '5se0004h' }, '', localize)).toBe('/ja/corpus/5se0004h')
    expect(redirectPath(corpus, 'collection', { lang: 'ja', id: '5se0004h' }, '?v=tok1', localize)).toBe('/ja/corpus/5se0004h?v=tok1')
    expect(redirectPath(corpus, 'collection', { lang: 'ja', id: 'codh:200014685:g' }, '?v=tok1', localize)).toBe('/ja/corpus/5se0004h?v=tok1')
    expect(redirectPath(corpus, 'corpus', { lang: 'ja', id: '5se0004h' }, '?v=tok1', localize)).toBeNull()
    expect(redirectPath({ id: '5se0004h' }, 'corpus', { lang: 'ja', id: '5se0004h' }, '', localize)).toBeNull()
    expect(redirectPath(record, 'collection', { lang: 'en', id: '3dh00000' }, '', localize)).toBeNull()
    expect(redirectPath(record, 'collection', { lang: 'en', id: 'hk:1:a' }, '?v=abc123', localize)).toBe('/en/crop/3dh00000?v=abc123')
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
