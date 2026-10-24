import { describe, expect, it } from 'bun:test'
import { viewerLinks } from '../src/lib/iiifViewers.js'

const manifest = 'https://x.org/m.json?a=1&b=2'

describe('a page\'s viewer links', () => {
  it('open the page at its canvas: the Curation Viewer by id, the Universal Viewer by 0-based position', () => {
    const links = viewerLinks({ manifest, canvas: 'https://x.org/c/30', canvas_index: 29 })
    expect(links.manifest).toBe(manifest)
    expect(new URL(links.curation).searchParams.get('manifest')).toBe(manifest)
    expect(new URL(links.curation).searchParams.get('canvas')).toBe('https://x.org/c/30')
    const hash = new URLSearchParams(new URL(links.universal).hash.slice(2))
    expect([hash.get('manifest'), hash.get('cv')]).toEqual([manifest, '29'])
  })
  it('open the book alone where the record does not place the page, and are none without a manifest', () => {
    const links = viewerLinks({ manifest, canvas: 'https://x.org/c/30', canvas_index: null })
    expect(new URL(links.curation).searchParams.has('canvas')).toBe(false)
    expect(new URLSearchParams(new URL(links.universal).hash.slice(2)).has('cv')).toBe(false)
    expect(viewerLinks({ manifest: 'javascript:alert(1)' })).toBeNull()
    expect(viewerLinks(null)).toBeNull()
  })
})
