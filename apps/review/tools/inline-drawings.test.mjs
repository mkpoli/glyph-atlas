import { describe, expect, it } from 'bun:test'
import { createInlineDrawings, drawingSvgUrl } from '../src/lib/composer/inline-drawings.js'

const decode = url => decodeURIComponent(url.slice('data:image/svg+xml,'.length))
const drawing = d => ({ d })
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

describe('the inline drawings of descriptions', () => {
  it('turns a drawing into an SVG mask for the 1000-unit box', () => {
    const url = drawingSvgUrl(drawing('M0 0L10 10Z'))
    expect(url.startsWith('data:image/svg+xml,')).toBe(true)
    const svg = decode(url)
    expect(svg).toContain('viewBox="0 0 1000 1000"')
    expect(svg).toContain('<path d="M0 0L10 10Z"/>')
    expect(svg).not.toContain('<mask')
  })

  it('masks the ink a cut removes', () => {
    const svg = decode(drawingSvgUrl({ d: 'M0 0L10 10Z', cut: 'M2 2L4 4Z' }))
    expect(svg).toContain('<mask id="cut">')
    expect(svg).toContain('<path d="M2 2L4 4Z" fill="#000"/>')
    expect(svg).toContain('<path d="M0 0L10 10Z" mask="url(#cut)"/>')
  })

  it('has no SVG for a drawing without ink', () => {
    expect(drawingSvgUrl(null)).toBeNull()
    expect(drawingSvgUrl({ d: '  ' })).toBeNull()
    expect(drawingSvgUrl({})).toBeNull()
  })

  it('draws one sequence at a time and keeps an answer once made', async () => {
    const started = []
    const answers = new Map()
    const asked = text => new Promise(resolve => { started.push(text); answers.set(text, resolve) })
    const inline = createInlineDrawings(asked)
    const first = inline('⿱䒑禺')
    const second = inline('⿰木口')
    await Promise.resolve()
    expect(started).toEqual(['⿱䒑禺'])
    answers.get('⿱䒑禺')(drawing('a'))
    expect(await first).toEqual(drawing('a'))
    await Promise.resolve()
    expect(started).toEqual(['⿱䒑禺', '⿰木口'])
    answers.get('⿰木口')(drawing('b'))
    expect(await second).toEqual(drawing('b'))
    expect(await inline('⿱䒑禺')).toEqual(drawing('a'))
    expect(started).toEqual(['⿱䒑禺', '⿰木口'])
  })

  it('shares a sequence asked for twice while it is drawn', async () => {
    let calls = 0
    const inline = createInlineDrawings(async () => { calls++; return drawing('a') })
    const [a, b] = await Promise.all([inline('⿱䒑禺'), inline('⿱䒑禺')])
    expect(a).toEqual(drawing('a'))
    expect(b).toEqual(drawing('a'))
    expect(calls).toBe(1)
  })

  it('asks a failed sequence again after the miss has passed', async () => {
    let calls = 0
    const inline = createInlineDrawings(async () => { calls++; return calls === 1 ? null : drawing('a') }, { missMs: 0 })
    expect(await inline('⿱䒑禺')).toBeNull()
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(await inline('⿱䒑禺')).toEqual(drawing('a'))
    expect(calls).toBe(2)
  })

  it('moves on after a drawing that never answers', async () => {
    const started = []
    const inline = createInlineDrawings(text => {
      started.push(text)
      return text === '⿱䒑禺' ? new Promise(() => {}) : Promise.resolve(drawing(text))
    }, { timeoutMs: 10 })
    expect(await inline('⿱䒑禺')).toBeNull()
    expect(await inline('⿰木口')).toEqual(drawing('⿰木口'))
    expect(started).toEqual(['⿱䒑禺', '⿰木口'])
  })
})
