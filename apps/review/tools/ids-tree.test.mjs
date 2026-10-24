import { describe, expect, it } from 'bun:test'
import { ALIASES, KEYS, OPERATORS, clear, leaves, nextEmpty, parse, partAt, paths, swapOp, unalias, unwrap, wrap, write } from '../src/lib/ids.js'

const tree = text => parse(text, { open: true }).tree

describe('a draft read as the editor reads it', () => {
  it('reads ？ and the parts still missing at the end as empty slots', () => {
    expect(tree('⿰亻')).toEqual({ op: '⿰', parts: ['亻', null] })
    expect(tree('⿰？木')).toEqual({ op: '⿰', parts: [null, '木'] })
    expect(tree('')).toBeNull()
    expect(tree('木')).toBe('木')
  })
  it('still names what is wrong', () => {
    expect(parse('⿰亻木林', { open: true }).problem).toBe('extra')
    expect(parse('⿰亻a', { open: true }).problem).toBe('component')
  })
  it('leaves a saved description as strict as before', () => {
    expect(parse('⿰亻').problem).toBe('missing')
    expect(parse('木').problem).toBe('character')
    expect(parse('⿰？木').tree).toEqual({ op: '⿰', parts: ['？', '木'] })
  })
  it('writes an empty slot as ？, both ways', () => {
    for (const text of ['⿰⿱匕失⿱コ疋', '⿲亻？木', '⿾疋', '⿻？？']) expect(write(tree(text))).toBe(text)
    expect(write(null)).toBe('？')
  })
})

describe('ASCII operator names', () => {
  it('name each operator but 〾 once', () => {
    const named = Object.values(ALIASES)
    expect(new Set(named).size).toBe(named.length)
    expect(OPERATORS.map(([op]) => op).filter(op => !named.includes(op))).toEqual(['〾'])
  })
  it('are written as their operators, read from the left', () => {
    expect(unalias('LR亻UD匕失')).toBe('⿰亻⿱匕失')
    expect(unalias('LLR')).toBe('⿲R')
    expect(unalias('RDOCXXMIROSU')).toBe('⿸⿴⿻⿾⿿㇯')
  })
})

describe('the editor verbs', () => {
  const t = tree('⿰亻⿱七木')
  it('split a slot: the part first, then empty slots', () => {
    expect(write(wrap('⿲', '木'))).toBe('⿲木？？')
    expect(wrap('⿾', '疋')).toEqual({ op: '⿾', parts: ['疋'] })
  })
  it('swap an operator and keep its parts', () => {
    expect(write(swapOp(t, [1], '⿻').tree)).toBe('⿰亻⿻七木')
    expect(write(swapOp(t, [], '⿲').tree)).toBe('⿲亻⿱七木？')
    expect(write(swapOp(tree('⿲亻木？'), [], '⿰').tree)).toBe('⿰亻木')
  })
  it('refuse a swap that would drop a filled part', () => {
    expect(swapOp(tree('⿲亻木口'), [], '⿰')).toEqual({ problem: 'drop' })
    expect(swapOp(t, [1], '⿾')).toEqual({ problem: 'drop' })
  })
  it('remove one level, keeping the first part that is filled', () => {
    expect(write(unwrap(t, [1]))).toBe('⿰亻七')
    expect(write(unwrap(tree('⿰？木'), []))).toBe('木')
    expect(unwrap(tree('⿰？？'), [])).toBeNull()
    expect(unwrap(t, [0])).toBe(t)
  })
  it('clear a slot', () => {
    expect(write(clear(t, [1, 0]))).toBe('⿰亻⿱？木')
    expect(clear(t, [])).toBeNull()
  })
})

describe('moving through a tree', () => {
  const t = tree('⿰？⿱七？')
  it('steps through every part in reading order', () => {
    expect(paths(t)).toEqual([[], [0], [1], [1, 0], [1, 1]])
    expect(leaves(t).map(leaf => leaf.char)).toEqual([null, '七', null])
    expect(partAt(t, [1, 1])).toBeNull()
  })
  it('finds the next empty slot, coming round', () => {
    expect(nextEmpty(t)).toEqual([0])
    expect(nextEmpty(t, [0])).toEqual([1, 1])
    expect(nextEmpty(t, [1, 1])).toEqual([0])
    expect(nextEmpty(tree('⿰亻木'))).toBeNull()
  })
})

describe('operator keys', () => {
  it('run along the number row, then q to y', () => {
    expect(KEYS['1']).toBe('⿰')
    expect(KEYS['=']).toBe('⿻')
    expect(KEYS.t).toBe('㇯')
    expect(KEYS.y).toBe('〾')
    expect(Object.keys(KEYS)).toHaveLength(OPERATORS.length)
  })
})
