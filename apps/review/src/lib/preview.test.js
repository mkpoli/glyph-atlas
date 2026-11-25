import { describe, expect, it } from 'bun:test'
import { defaultRoom, groupMessages, linkify, roomLabel, roomLang, senderMark } from './preview.js'

describe('preview room helpers', () => {
  const t = key => ({
    'contact.preview.room.welcome': 'Welcome',
    'contact.preview.room.dev': 'Dev',
  }[key] ?? key)

  it('localizes topic rooms and keeps language rooms in their own script', () => {
    expect(roomLabel({ alias: '#welcome:glyphatlas.org', name: '歡迎 · Welcome' }, t)).toBe('Welcome')
    expect(roomLabel({ alias: '#ja:glyphatlas.org', name: '日本語' }, t)).toBe('日本語')
    expect(roomLang({ alias: '#ja:glyphatlas.org' })).toBe('ja')
    expect(roomLang({ alias: '#welcome:glyphatlas.org' })).toBeUndefined()
  })

  it('picks the room matching the page language when present, falling back to the first room', () => {
    const rooms = [
      { alias: '#welcome:glyphatlas.org' },
      { alias: '#en:glyphatlas.org' },
      { alias: '#ja:glyphatlas.org' },
    ]
    expect(defaultRoom(rooms, 'ja')).toBe('#ja:glyphatlas.org')
    expect(defaultRoom(rooms, 'ko')).toBe('#welcome:glyphatlas.org')
    expect(defaultRoom([], 'en')).toBeNull()
  })

  it('maps bridged networks to their LinkMark icons and native handles to matrix', () => {
    expect(senderMark('Discord')).toBe('discord')
    expect(senderMark('Telegram')).toBe('telegram')
    expect(senderMark('QQ')).toBe('qq')
    expect(senderMark('IRC')).toBe('irc')
    expect(senderMark('mkpoli')).toBe('matrix')
  })
})

describe('groupMessages', () => {
  it('merges consecutive messages from the same sender within five minutes', () => {
    const base = new Date('2026-11-25T17:40:00Z').getTime()
    const groups = groupMessages([
      { name: 'Telegram', body: 'one', at: base },
      { name: 'Telegram', body: 'two', at: base + 120_000 },
      { name: 'Telegram', body: 'later', at: base + 10 * 60_000 },
      { name: 'Discord', body: 'reply', at: base + 10 * 60_000 + 1000 },
    ])
    expect(groups).toHaveLength(3)
    expect(groups[0].items.map(m => m.body)).toEqual(['one', 'two'])
    expect(groups[1].items.map(m => m.body)).toEqual(['later'])
    expect(groups[2].name).toBe('Discord')
  })
})

describe('linkify', () => {
  it('extracts http(s) links and keeps trailing sentence punctuation outside the href', () => {
    expect(linkify('see (https://github.com/mkpoli/glyph-atlas/pull/1281).')).toEqual([
      { type: 'text', text: 'see (' },
      { type: 'link', href: 'https://github.com/mkpoli/glyph-atlas/pull/1281', label: 'github.com/mkpoli/glyph-atlas/pull/1281' },
      { type: 'text', text: ').' },
    ])
  })

  it('preserves balanced parentheses inside URLs', () => {
    expect(linkify('https://en.wikipedia.org/wiki/Kanji_(disambiguation), ok')).toEqual([
      { type: 'link', href: 'https://en.wikipedia.org/wiki/Kanji_(disambiguation)', label: 'en.wikipedia.org/wiki/Kanji_(disambiguation)' },
      { type: 'text', text: ', ok' },
    ])
  })

  it('stops before CJK punctuation and adjacent prose without spaces', () => {
    expect(linkify('参考（https://example.org。次の文）')).toEqual([
      { type: 'text', text: '参考（' },
      { type: 'link', href: 'https://example.org', label: 'example.org' },
      { type: 'text', text: '。次の文）' },
    ])
  })
})
