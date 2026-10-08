import { describe, expect, it } from 'bun:test'
import { keyText } from '../src/lib/codePoints.js'

describe('a code point key', () => {
  it('names the text of its code points', () => {
    expect(keyText('U+4EEE')).toBe('仮')
    expect(keyText('U+1112 U+119E')).toBe('ᄒᆞ')
    expect(keyText('u+304b  u+309a')).toBe('か゚')
  })
  it('names nothing past U+10FFFF or when it is no key', () => {
    expect(keyText('U+FFFFFF')).toBeNull()
    expect(keyText('U+4EEE U+110000')).toBeNull()
    expect(keyText('仮')).toBeNull()
    expect(keyText('')).toBeNull()
  })
})
