import { describe, expect, it } from 'bun:test'
import { isDescription, parse, tokenize } from '../src/lib/ids.js'
import { scriptParts } from '../src/lib/identity.js'
import { tokens } from '../src/lib/decompose.js'
import { composerText } from '../src/lib/composer/parts.js'

describe('a saved form holding a part no code point names', () => {
  it('reads the part as one component', () => {
    expect(isDescription('⿱&X-00087;龴')).toBe(true)
    expect(parse('⿱&X-00087;龴').tree).toEqual({ op: '⿱', parts: ['&X-00087;', '龴'] })
    expect(tokenize('⿱&X-00087;龴').map(token => [token.start, token.end])).toEqual([[0, 11], [1, 10], [10, 11]])
    expect(tokens('⿱&X-00087;龴')).toEqual(['⿱', '&X-00087;', '龴'])
    expect(parse('⿱&X-0087;龴').problem).toBe('component')
  })
  it('stands as one part of a text', () => {
    expect(scriptParts('⿱&X-00087;龴令').map(part => part.text)).toEqual(['⿱&X-00087;龴', '令'])
  })
  it('gives the composer the name it draws the part by', async () => {
    expect(await composerText('⿱&X-00087;龴')).toBe('⿱⿵𠆢一龴')
    expect(await composerText('⿰&X-00005;又')).toBe('⿰{5}又')
    expect(await composerText('⿰&X-00865;木')).toBe('⿰⿰丿𠃌木')
    expect(await composerText('⿺辶𦊷')).toBe('⿺辶𦊷')
  })
})
