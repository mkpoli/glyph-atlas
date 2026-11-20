import { describe, expect, it } from 'bun:test'
import { offeredFigures, offeredShapes } from '../src/lib/formOffers.js'

describe('form offers', () => {
  it('leaves out the figure a font draws for the character alone', () => {
    // 邦's five Moji_Joho figures; MJ026242 is the default.
    const figures = [['3', 'MJ026242', true], ['4', 'MJ026246', false], ['5', 'MJ026244', false]]
      .map(([selector, mj, isDefault]) => ({ char: '邦' + selector, mj, default: isDefault }))
    expect(offeredFigures(figures).map(figure => figure.mj)).toEqual(['MJ026246', 'MJ026244'])
  })

  it('leaves out the regional shape that holds in Japan', () => {
    const shapes = [{ text: '⿰丰阝', regions: 'GHTJKV' }, { text: '⿰⿻㇒𰀁阝', regions: 'P' }]
    expect(offeredShapes(shapes).map(shape => shape.text)).toEqual(['⿰⿻㇒𰀁阝'])
  })

  it('keeps the shapes of other regions where Japan has its own', () => {
    // 突 as /layers/ids describes it.
    const shapes = [{ text: '⿱穴犬', regions: 'G' }, { text: '⿱⿱宀儿犬', regions: 'HTKPV' }, { text: '⿱⿱宀儿大', regions: 'J' }]
    expect(offeredShapes(shapes).map(shape => shape.text)).toEqual(['⿱穴犬', '⿱⿱宀儿犬'])
  })
})
