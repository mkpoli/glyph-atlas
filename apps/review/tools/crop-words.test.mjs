import { afterAll, beforeEach, describe, expect, it, mock } from 'bun:test'

const request = mock()
mock.module('../src/lib/client.js', () => ({ request }))
const { clearWord, setWord, wordChoices } = await import('../src/lib/cropWords.js')
afterAll(() => mock.restore())
beforeEach(() => request.mockReset())

const crop = () => ({ id: 'crop/' + crypto.randomUUID(), crop_version: 'pixels', label: '計', written_character: '許', word: null })
const value = { word: 'ja/ばかり/副助詞', method: 'ruby', form: 'probable' }

describe('the crop word client', () => {
  it('asks for the deduplicated characters and a reading, respecting code points', async () => {
    request.mockResolvedValue({ items: [{ id: value.word }] })
    expect(await wordChoices({ label: '𠀀計', written_character: '計許' }, 'バカ')).toEqual([{ id: value.word }])
    const [path] = request.mock.calls[0], url = new URL(path, 'https://atlas.test')
    expect(url.pathname).toBe('/layers/word-choices')
    expect(url.searchParams.get('q')).toBe('バカ')
    expect(url.searchParams.get('c')).toBe('𠀀計許')
    await wordChoices({ label: '一二三四五', written_character: '六' })
    expect(new URL(request.mock.calls[1][0], 'https://atlas.test').searchParams.get('c')).toBe('一二三四')
  })

  it('reuses a submission after a lost answer, then gives an answered value a new one', async () => {
    const item = crop(), slot = { status: 'asserted', values: [{ word: value.word, form: 'probable' }] }
    request.mockRejectedValueOnce(new TypeError('network')).mockResolvedValue({ word: slot, replaced: null })
    await expect(setWord(item, value)).rejects.toThrow('network')
    expect(await setWord(item, value)).toEqual({ crop: { ...item, word: slot }, replaced: null })
    await setWord(item, value)
    const bodies = request.mock.calls.map(([, body]) => body)
    expect(bodies[0]).toEqual(bodies[1])
    expect(bodies[2].id).not.toBe(bodies[1].id)
    expect(bodies[1]).toMatchObject({ crop_version: item.crop_version, ...value })
    expect(request.mock.calls[0][0]).toBe(`/atlas/characters/${encodeURIComponent(item.id)}/word`)
  })

  it('drops a refused submission, and keeps versions and values separate', async () => {
    const item = crop()
    request.mockRejectedValueOnce(Object.assign(new Error('stale crop'), { status: 409 })).mockResolvedValue({ word: null })
    await expect(setWord(item, value)).rejects.toThrow('stale crop')
    await setWord(item, value)
    expect(request.mock.calls[0][1].id).not.toBe(request.mock.calls[1][1].id)
    request.mockRejectedValue(new TypeError('offline'))
    for (const [record, choice] of [[item, value], [{ ...item, crop_version: 'recut' }, value], [item, { ...value, method: 'note' }], [item, { ...value, form: 'certain' }]]) {
      await expect(setWord(record, choice)).rejects.toThrow('offline')
    }
    expect(new Set(request.mock.calls.slice(2).map(([, body]) => body.id)).size).toBe(4)
  })

  it('clears with a null word and keeps the answered replacement', async () => {
    const item = crop()
    request.mockResolvedValue({ word: null, replaced: value })
    expect(await clearWord(item)).toEqual({ crop: { ...item, word: null }, replaced: value })
    expect(request.mock.calls[0][1]).toEqual({ id: expect.any(String), crop_version: 'pixels', word: null })
  })
})
