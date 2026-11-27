import { afterAll, beforeEach, describe, expect, it, mock } from 'bun:test'

mock.module('kanji-readings', () => ({ SHARDS: 7, PREFIX: '/kanji-readings/' }))
const files = { [`/kanji-readings/${'器'.codePointAt(0) % 7}.json`]: { 器: ['うつわ', 'き'] }, [`/kanji-readings/${'諸'.codePointAt(0) % 7}.json`]: { 諸: ['しょ', 'もろもろ'] } }
const asked = [], realFetch = globalThis.fetch
globalThis.fetch = mock(async url => { asked.push(url); return files[url] ? new Response(JSON.stringify(files[url])) : new Response('', { status: 404 }) })
const { kanjiReadings } = await import('../src/lib/kanjiReadings.js')
afterAll(() => { mock.restore(); globalThis.fetch = realFetch })
beforeEach(() => { asked.length = 0 })

describe('a kanji\'s readings from the dictionary', () => {
  it('gives each kanji of a run its readings, and kana or an unknown kanji none', async () => {
    expect([...await kanjiReadings(['諸', '器', 'に', '𠀋'])]).toEqual([['諸', ['しょ', 'もろもろ']], ['器', ['うつわ', 'き']]])
  })
  it('reads each file once, and a character with a variation selector as its base', async () => {
    await kanjiReadings(['器'])
    asked.length = 0
    expect([...await kanjiReadings(['器', '器\u{E0101}'])]).toEqual([['器', ['うつわ', 'き']], ['器\u{E0101}', ['うつわ', 'き']]])
    expect(asked).toEqual([])
  })
  it('asks again for a file that could not be read', async () => {
    const char = '𠀋', url = `/kanji-readings/${char.codePointAt(0) % 7}.json`
    expect((await kanjiReadings([char])).size).toBe(0)
    files[url] = { [char]: ['じょう'] }
    expect([...await kanjiReadings([char])]).toEqual([[char, ['じょう']]])
  })
})
