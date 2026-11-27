import { describe, expect, it } from 'bun:test'
import { descriptionEnd, isDescription, parse, tokenize, units } from '../src/lib/ids.js'
import { scriptParts } from '../src/lib/identity.js'
import { lookupChar, tokens } from '../src/lib/decompose.js'
import { parseDescription, tooLarge } from 'han-compose'
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
  it('gives the composer the best drawing it holds for the part, and nothing for a form holding a part with none', async () => {
    // The composer's own cut of the part from a real glyph, a Han character the tables confirm, then a confirmed description.
    expect(await composerText('⿹&X-00028;貝')).toBe('⿹&CDP-8CB8;貝')
    expect(await composerText('⿱&X-00087;龴')).toBe('⿱亼龴')
    expect(await composerText('⿰&X-00865;木')).toBe('⿰勹木')
    expect(await composerText('⿱&X-00783;一')).toBe('⿱⿰丨丨一')
    expect(await composerText('⿺辶𦊷')).toBe('⿺辶𦊷')
    for (const text of ['⿰&X-00053;又', '⿱&X-99999;マ']) expect(await composerText(text)).toBeNull()
  })
  it('measures a description as the composer is given it', async () => {
    // Each part draws as several components, so the description is too large only once they are written out.
    const part = '&X-00626;', text = `⿰⿰⿰${part}${part}⿰${part}${part}⿰⿰${part}${part}木`
    expect(tooLarge(parse(text, { open: true }).tree)).toBe(false)
    expect(tooLarge(parseDescription(await composerText(text)))).toBe(true)
  })
  it('names no part the composer cannot read', () => {
    for (const drawing of Object.values(drawings)) expect(drawing).not.toMatch(/[？{]/u)
  })
})
