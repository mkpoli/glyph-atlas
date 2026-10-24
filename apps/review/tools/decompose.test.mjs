import { describe, expect, it } from 'bun:test'
import { decomposable, lookupChar, namedParts, roots, searchParts, tokens, treeKey, unencoded, variants } from '../src/lib/decompose.js'

const ready = (sequences, shapes = []) => ({ status: 'ready', sequences, shapes })

describe('what the box decomposes', () => {
  it('reads a description, typed with operators or their ASCII names, as one sequence', () => {
    expect(roots('⿰亻木')).toEqual([{ kind: 'sequence', text: '⿰亻木', tree: { op: '⿰', parts: ['亻', '木'] } }])
    expect(roots('LR亻木')[0].text).toBe('⿰亻木')
  })
  it('reads anything else as its component characters, once each, in order', () => {
    expect(roots('疑 骨疑')).toEqual([{ kind: 'char', char: '疑' }, { kind: 'char', char: '骨' }])
    expect(roots('⿰亻')).toEqual([{ kind: 'char', char: '亻' }])
    expect(roots('abc？')).toEqual([])
    expect(roots('')).toEqual([])
  })
  it('keeps a variation selector with its character and asks for the character alone', () => {
    expect(roots('葛\u{E0100}')).toEqual([{ kind: 'char', char: '葛\u{E0100}' }])
    expect(lookupChar('葛\u{E0100}')).toBe('葛')
    expect(decomposable('？')).toBe(false)
    expect(decomposable(null)).toBe(false)
  })
  it('takes at most eight characters', () => {
    expect(roots('一二三四五六七八九十')).toHaveLength(8)
  })
})

describe('a character\'s descriptions', () => {
  it('lists regional shapes first with their regions, then the other sequences', () => {
    const found = variants(ready(['⿰亻木', '⿳𭁟冖⺝', '⿳𭁟冖⺼'], [{ text: '⿳𭁟冖⺼', regions: 'T' }, { text: '⿳𭁟冖⺝', regions: 'HJKP' }]))
    expect(found.map(v => [v.text, v.regions])).toEqual([['⿳𭁟冖⺼', 'T'], ['⿳𭁟冖⺝', 'HJKP'], ['⿰亻木', '']])
    expect(found[0].tree).toEqual({ op: '⿳', parts: ['𭁟', '冖', '⺼'] })
  })
  // The structure route drops these today; the box still has to show one that reaches it.
  it('keeps a sequence naming an unencoded component as text, with its encoded parts', () => {
    const [found] = variants(ready(['⿹{22}一']))
    expect(found.tree).toBeNull()
    expect(tokens('⿹{22}一')).toEqual(['⿹', '{22}', '一'])
    expect(unencoded('{22}')).toBe('22')
    expect(unencoded('一')).toBeNull()
    expect(namedParts('⿱⿰{27}{27}一')).toEqual(['一'])
  })
  it('has none while loading', () => {
    expect(variants(undefined)).toEqual([])
    expect(variants({ status: 'loading' })).toEqual([])
  })
})

describe('the parts the component search is asked for', () => {
  it('asks for two or more typed characters as they are', () => {
    expect(searchParts(roots('匕矢'), {})).toBe('匕矢')
  })
  it('asks for the components of one typed character, counted, ideographs and strokes only', () => {
    expect(searchParts(roots('疑'), { 疑: ready(['⿰𠤕⿱マ疋']) })).toBe('𠤕疋')
    expect(searchParts(roots('林'), { 林: ready(['⿰木木']) })).toBe('木木')
    expect(searchParts(roots('⿰木木'), {})).toBe('木木')
  })
  it('asks for nothing with fewer than two', () => {
    expect(searchParts(roots('口'), { 口: ready([]) })).toBe('')
    expect(searchParts(roots('疑'), {})).toBe('')
  })
})

describe('keys in the tree', () => {
  // 疑 (open) › ⿰𠤕⿱龴疋 (open) › 𠤕 (closed), ⿱龴疋 (open) › 龴 (two swaps), 疋
  const rows = [
    { level: 1, open: true, openable: true },
    { level: 2, open: true, openable: true },
    { level: 3, open: false, openable: true },
    { level: 3, open: true, openable: true },
    { level: 4, open: false, openable: false, swaps: [{ char: 'コ' }, { char: 'マ' }] },
    { level: 4, open: false, openable: true },
  ]
  it('steps down and up, and to the ends', () => {
    expect(treeKey(rows, 0, 'ArrowDown')).toEqual({ focus: 1 })
    expect(treeKey(rows, 5, 'ArrowDown')).toEqual({ focus: 5 })
    expect(treeKey(rows, 0, 'ArrowUp')).toEqual({ focus: 0 })
    expect(treeKey(rows, 3, 'End')).toEqual({ focus: 5 })
    expect(treeKey(rows, 3, 'Home')).toEqual({ focus: 0 })
  })
  it('opens a closed row and steps into an open one', () => {
    expect(treeKey(rows, 2, 'ArrowRight')).toEqual({ toggle: 2 })
    expect(treeKey(rows, 3, 'ArrowRight')).toEqual({ focus: 4 })
    expect(treeKey(rows, 5, 'ArrowRight')).toEqual({ toggle: 5 })
  })
  it('steps through a part\'s swaps and back', () => {
    expect(treeKey(rows, 4, 'ArrowRight')).toEqual({ swap: 0 })
    expect(treeKey(rows, 4, 'ArrowRight', 0)).toEqual({ swap: 1 })
    expect(treeKey(rows, 4, 'ArrowRight', 1)).toEqual({ focus: 4 })
    expect(treeKey(rows, 4, 'ArrowLeft', 1)).toEqual({ swap: 0 })
    expect(treeKey(rows, 4, 'ArrowLeft', 0)).toEqual({ swap: null })
  })
  it('closes an open row and steps out to the parent', () => {
    expect(treeKey(rows, 3, 'ArrowLeft')).toEqual({ toggle: 3 })
    expect(treeKey(rows, 4, 'ArrowLeft')).toEqual({ focus: 3 })
    expect(treeKey(rows, 2, 'ArrowLeft')).toEqual({ focus: 1 })
  })
  it('leaves other keys alone', () => {
    expect(treeKey(rows, 0, 'a')).toBeNull()
    expect(treeKey([], 0, 'ArrowDown')).toBeNull()
  })
})
