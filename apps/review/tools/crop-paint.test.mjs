import { describe, expect, it } from 'bun:test'
import { cropPaint, cropShape, cropTone } from '../src/lib/cropPaint.js'

const box = { x: 10, y: 20, w: 40, h: 50 }

describe('what a crop paints before its image arrives', () => {
  it('is shaped by the image size its record names', () => {
    expect(cropShape({ image: '/atlas/media/a.webp', box, image_size: [47, 58] })).toEqual([47, 58])
  })
  it('falls back to its box with the margin the site cuts its own crops with', () => {
    expect(cropShape({ image: '/atlas/media/a.webp', box })).toEqual([48, 58])
    expect(cropShape({ image: '/atlas/media/a.webp', box, source_scale: [2, 2] })).toEqual([96, 116])
  })
  it('takes a holder\'s image as its box alone', () => {
    expect(cropShape({ image: 'https://example.org/iiif/p/10,20,40,50/480,/0/default.jpg', box })).toEqual([40, 50])
  })
  it('has no shape without a size or a box, and paints a square', () => {
    expect(cropShape({ image: '/atlas/media/a.webp' })).toBeNull()
    expect(cropPaint({})).toContain("viewBox='0 0 1 1'")
  })
  it('carries the record\'s tone, and only a colour', () => {
    expect(cropPaint({ tone: '#d8cfbf', image_size: [47, 58] })).toBe(
      "--tone:#d8cfbf;--crop-shape:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 47 58'%3E%3Crect width='47' height='58'/%3E%3C/svg%3E\")")
    expect(cropTone({ tone: 'red;background:url(x)' })).toBe('')
    expect(cropTone({})).toBe('')
    expect(cropPaint({ tone: null, image_size: [1, 2] })).not.toContain('--tone')
  })
  it('lets the loaded image\'s own size reshape it', () => {
    expect(cropPaint({ image_size: [10, 10] }, [30, 40])).toContain("viewBox='0 0 30 40'")
  })
})
