import { afterAll, beforeEach, describe, expect, it, mock } from 'bun:test'

const request = mock()
mock.module('../src/lib/client.js', () => ({ request }))
const { wordChoices } = await import('../src/lib/cropWords.js')
afterAll(() => mock.restore())
beforeEach(() => request.mockReset())


describe('the crop word client', () => {
  it('asks for the deduplicated characters and a reading, respecting code points', async () => {
    request.mockResolvedValue({ items: [{ id: 'ja/ばかり/副助詞' }] })
    expect(await wordChoices({ label: '𠀀計', written_character: '計許' }, 'バカ')).toEqual([{ id: 'ja/ばかり/副助詞' }])
    const [path] = request.mock.calls[0], url = new URL(path, 'https://atlas.test')
    expect(url.pathname).toBe('/layers/word-choices')
    expect(url.searchParams.get('q')).toBe('バカ')
    expect(url.searchParams.get('c')).toBe('𠀀計許')
    await wordChoices({ label: '一二三四五', written_character: '六' })
    expect(new URL(request.mock.calls[1][0], 'https://atlas.test').searchParams.get('c')).toBe('一二三四')
  })
})
