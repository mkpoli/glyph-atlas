import { describe, expect, it } from 'bun:test'
import { boxLayout, boxSize, height, readSequence, tint } from '../src/lib/idsView.js'
import { write } from '../src/lib/ids.js'

describe('a description read to be shown', () => {
  it('reads every operator, one part to three', () => {
    for (const text of ['⿱自犬', '⿳吅犬吅', '⿰⿱匕失⿱コ疋', '⿾疋', '⿿予', '〾⿻一乚', '㇯鸟丶', '⿰亻？']) expect(write(readSequence(text))).toBe(text)
  })
  it('takes an unencoded component and a variation selector as one part', () => {
    expect(readSequence('⿰{18}卩')).toEqual({ op: '⿰', parts: ['{18}', '卩'] })
    expect(readSequence('⿰突󠄃{7}')).toEqual({ op: '⿰', parts: ['突󠄃', '{7}'] })
  })
  it('reads nothing from what is no whole description', () => {
    for (const text of ['', undefined, '臭', '⿰亻', '⿰亻木林', '⿰亻 木', '{18}']) expect(readSequence(text)).toBeNull()
  })
  it('counts the operators over the deepest part', () => {
    expect(['⿱自犬', '⿰⿱匕失疋', '⿰亻⿱⿰口口大'].map(text => height(readSequence(text)))).toEqual([1, 2, 3])
  })
})

describe('the colours of its layers', () => {
  it('gives an operator and its parts one hue, and an operator inside another', () => {
    expect(tint([], true)).toBe(tint([0]))
    expect(tint([0])).toBe(tint([1]))
    expect(tint([1], true)).toBe(tint([1, 0]))
    expect(new Set([tint([], true), tint([0], true), tint([1], true), tint([0, 0], true), tint([0, 1], true)]).size).toBe(5)
  })
})

describe('its boxes', () => {
  it('follow the operators', () => {
    const way = text => boxLayout(readSequence(text))
    expect(['⿰亻木', '⿲亻木木', '⿱自犬', '⿴囗玉', '⿻一乚', '㇯鸟丶', '〾木'].map(way)).toEqual(['row', 'row', 'column', 'enclosure', 'column', 'column', 'column'])
    expect(way('⿸⿰厂丶木')).toBe('column')
  })
  it('take more room with more parts', () => {
    const [two, three, nested] = ['⿰亻木', '⿲亻木木', '⿰亻⿱木木'].map(text => boxSize(readSequence(text)))
    expect(three.width).toBeGreaterThan(two.width)
    expect(nested.height).toBeGreaterThan(two.height)
    expect(boxSize('木')).toEqual({ width: 1, height: 1 })
  })
})
