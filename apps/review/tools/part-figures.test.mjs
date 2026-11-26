import { describe, expect, it } from 'bun:test'
import { parse } from '../src/lib/ids.js'
import { componentVariantForms, drawnFigures, grassForm, grassIVS, grassShape } from '../src/lib/partFigures.js'

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
  it('list the plain character first when a component has drawn IVS forms', () => {
    expect(componentVariantForms('者', ['者\u{E0101}'], [{ char: '者\u{E0101}', mj: 'MJ020504' }])).toEqual([
      { char: '者', mj: null },
      { char: '者\u{E0101}', mj: 'MJ020504' },
    ])
    expect(componentVariantForms('侮', ['侮\uFE00'], [])).toEqual([])
    expect(componentVariantForms('一', [], [])).toEqual([])
  })
  it('recognise grass radicals and top-row ⿰十十 for grass form selection', () => {
    expect(grassForm('艹', [0], parse('⿱艹化').tree)).toBe('艹')
    expect(grassForm(grassIVS.split, [0], parse(`⿱${grassIVS.split}化`).tree)).toBe('⺿')
    expect(grassForm(grassIVS.inward, [0], parse(`⿱${grassIVS.inward}化`).tree)).toBe('⻀')
    expect(grassShape(grassIVS.connected)).toBe('艹')
    const topSplit = parse('⿱⿰十十化').tree
    expect(grassForm(topSplit.parts[0], [0], topSplit)).toBe('⺿')
    const sideSplit = parse('⿰⿰十十化').tree
    expect(grassForm(sideSplit.parts[0], [0], sideSplit)).toBeNull()
    expect(grassForm('木', [0], parse('⿰木口').tree)).toBeNull()
  })
})
