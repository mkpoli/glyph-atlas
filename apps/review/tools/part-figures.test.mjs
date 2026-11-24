import { describe, expect, it } from 'bun:test'
import { drawnFigures, figuresFor, partName } from '../src/lib/partFigures.js'

describe('the figures of a part', () => {
  it('are the sequences the export draws, with their MJ figure where one is registered', () => {
    const registered = [{ char: '辶\u{E0102}', mj: 'MJ030302', default: false }]
    expect(drawnFigures('辶', '\u{E0101}', registered)).toEqual([{ char: '辶\u{E0101}', mj: null }])
    expect(drawnFigures('辶', '\u{E0101}\u{E0102}', registered)[1]).toEqual({ char: '辶\u{E0102}', mj: 'MJ030302' })
  })
  it('are named in a title by their sequence', () => {
    expect([partName('辶\u{E0101}'), partName('⿴囗丷')]).toEqual(['U+8FB6 U+E0101', '⿴囗丷'])
  })
  it('are none for a character the table does not list', async () => {
    expect(drawnFigures('一', undefined, [])).toEqual([])
    expect(await figuresFor('⿺')).toEqual([])
  })
})
