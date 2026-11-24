import { describe, expect, it } from 'bun:test'
import { drawnFigures } from '../src/lib/partFigures.js'

describe('the figures of a part', () => {
  it('are the sequences the export draws, with their MJ figure where one is registered', () => {
    const registered = [{ char: '辶\u{E0102}', mj: 'MJ030302', default: false }]
    expect(drawnFigures('辶', '\u{E0101}', registered)).toEqual([{ char: '辶\u{E0101}', mj: null }])
    expect(drawnFigures('辶', '\u{E0101}\u{E0102}', registered)[1]).toEqual({ char: '辶\u{E0102}', mj: 'MJ030302' })
  })
  it('leave out a standardized sequence, and are none for a character the export does not list', () => {
    expect(drawnFigures('侮', '\uFE00\u{E0101}', [])).toEqual([{ char: '侮\u{E0101}', mj: null }])
    expect(drawnFigures('一', undefined, [])).toEqual([])
  })
})
