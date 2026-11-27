import { describe, expect, it } from 'bun:test'
import { descriptionEnd, isDescription, parse, tokenize, units } from '../src/lib/ids.js'
import { scriptParts } from '../src/lib/identity.js'
import { lookupChar, tokens } from '../src/lib/decompose.js'
import { composerText } from '../src/lib/composer/parts.js'
import drawings from '../src/lib/customPartDrawings.js'

describe('a saved form holding a part no code point names', () => {
  it('reads the part as one component', () => {
    expect(isDescription('⿱&X-00087;龴')).toBe(true)
    expect(parse('⿱&X-00087;龴').tree).toEqual({ op: '⿱', parts: ['&X-00087;', '龴'] })
    expect(tokenize('⿱&X-00087;龴').map(token => [token.start, token.end])).toEqual([[0, 11], [1, 10], [10, 11]])
    expect(tokens('⿱&X-00087;龴')).toEqual(['⿱', '&X-00087;', '龴'])
    expect(parse('⿱&X-0087;龴').problem).toBe('component')
    expect(parse('⿱&X-00087;\uFE00龴').problem).toBe('component')
  })
  it('stands as one part of a text', () => {
    expect(scriptParts('⿱&X-00087;龴令').map(part => part.text)).toEqual(['⿱&X-00087;龴', '令'])
  })
  it('takes no variation selector after it, wherever a description is read', () => {
    const text = '⿱&X-00087;\uFE00龴令'
    expect(descriptionEnd(units(text), 0)).toBe(-1)
    expect(scriptParts(text).some(part => part.text.startsWith('⿱&X-00087;\uFE00'))).toBe(false)
    expect(tokens('⿱&X-00087;\uFE00龴')).toEqual(['⿱', '&X-00087;', '\uFE00', '龴'])
  })
  it('asks the character route nothing about it', () => {
    expect(lookupChar('&X-00087;')).toBe('')
    expect(lookupChar('令\uFE00')).toBe('令')
  })
  it('gives the composer the drawing chosen for the part, and nothing for a form holding a part with none', async () => {
    expect(await composerText('⿱&X-00087;龴')).toBe('⿱⿵𠆢一龴')
    expect(await composerText('⿺辶𦊷')).toBe('⿺辶𦊷')
    for (const text of ['⿰&X-00005;又', '⿱&X-00783;一', '⿰&X-00865;木', '⿱&X-99999;マ']) expect(await composerText(text)).toBeNull()
  })
  it('draws parts only in encoded characters', () => {
    for (const drawing of Object.values(drawings)) expect(drawing).not.toMatch(/[？{&]|#\(/u)
  })
})
