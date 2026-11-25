import { describe, expect, it } from 'bun:test'
import { redrawable } from '../src/lib/cropBox.js'
import { cropPixels } from '../src/lib/identity.js'

const box = { x: 10, y: 20, w: 30, h: 40 }
const contextBox = { x: 0, y: 0, w: 200, h: 200 }

describe('redrawable crop detection', () => {
  it('allows redrawing collected and corpus crops that carry source context bounds', () => {
    expect(redrawable({ context: true, context_image: '/atlas/media/ctx.webp', context_box: contextBox, crop_box: box })).toBe(true)
    expect(redrawable({ origin: 'corpus', context_image: 'https://example.org/ctx.jpg', context_box: contextBox, crop_box: box, proxyable: true })).toBe(true)
  })

  it('refuses crops lacking context, disabled explicitly, unproxyable, or marked non-editable', () => {
    expect(redrawable({ origin: 'corpus', box, proxyable: true })).toBe(false)
    expect(redrawable({ context: true, context_box: contextBox, crop_box: box })).toBe(false)
    expect(redrawable({ context: false, context_image: 'https://example.org/ctx.jpg', context_box: contextBox, crop_box: box })).toBe(false)
    expect(redrawable({ origin: 'corpus', context_image: 'https://example.org/ctx.jpg', context_box: contextBox, crop_box: box, proxyable: false })).toBe(false)
    expect(redrawable({ context: true, context_image: '/atlas/media/ctx.webp', context_box: contextBox, crop_box: box, crop_editable: false })).toBe(false)
    expect(redrawable({ origin: 'corpus', context_image: 'https://example.org/ctx.jpg', context_box: contextBox, crop_box: box, source_scale: [0, 1] })).toBe(false)
    expect(redrawable(null)).toBe(false)
  })
})

describe('crop pixel identity helper', () => {
  it('keys exclusively by origin instead of guessing from mixed fields', () => {
    expect(cropPixels({ origin: 'corpus', source_revision: 'rev1', image_sha256: 'hash1' })).toEqual({ source_revision: 'rev1' })
    expect(cropPixels({ origin: 'local', source_revision: 'rev1', image_sha256: 'hash1' })).toEqual({ image_sha256: 'hash1' })
    expect(cropPixels({ origin: 'local', image: '/i.webp' }, { withImage: true })).toEqual({ image: '/i.webp' })
    expect(cropPixels({ origin: 'corpus' })).toEqual({})
  })
})
