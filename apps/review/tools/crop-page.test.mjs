import { afterAll, describe, expect, it, mock } from 'bun:test'

class HttpRedirect { constructor(status, location) { this.status = status; this.location = location } }
class HttpError { constructor(status, body) { this.status = status; this.body = body } }

const mocks = () => {
  mock.module('@sveltejs/kit', () => ({
    redirect(status, location) { throw new HttpRedirect(status, location) },
    error(status, body) { throw new HttpError(status, body) },
  }))
  mock.module('../src/lib/i18n.svelte.js', () => ({
    localize: (path, lang) => `/${lang}${path}`,
    t: k => k,
    formatNumber: String,
  }))
  mock.module('../src/lib/citedCut.js', () => ({
    citedCut: async (record, token) => ({ id: record.id, token }),
  }))
}

mocks()
const { loadCropPage } = await import('../src/lib/cropPage.js')
afterAll(() => mock.restore())

describe('crop page loader', () => {
  it('redirects moved or retired crop ids to /crop/[id] preserving citation query', async () => {
    const read = async () => ({ id: '5se0004h', source_id: 'codh:x:1', origin: 'corpus' })
    let caught = null
    try {
      await loadCropPage(read, {
        fetch: () => {},
        params: { lang: 'ja', id: 'codh:x:1' },
        url: new URL('https://glyphatlas.org/ja/crop/codh%3Ax%3A1?v=tok1'),
      })
    } catch (e) { caught = e }
    expect(caught).toBeInstanceOf(HttpRedirect)
    expect(caught.status).toBe(302)
    expect(caught.location).toBe('/ja/crop/5se0004h?v=tok1')
  })

  it('loads both local and corpus crops at /crop/[id] without redirecting', async () => {
    const local = { id: '3dh00000', label: '一' }
    const corpus = { id: '5se0004h', origin: 'corpus', label: '翁' }
    expect(await loadCropPage(async () => local, {
      fetch: () => {},
      params: { lang: 'en', id: '3dh00000' },
      url: new URL('https://glyphatlas.org/en/crop/3dh00000'),
    })).toEqual({ record: local, cited: null })
    expect(await loadCropPage(async () => corpus, {
      fetch: () => {},
      params: { lang: 'ja', id: '5se0004h' },
      url: new URL('https://glyphatlas.org/ja/crop/5se0004h?v=v1'),
    })).toEqual({ record: corpus, cited: { id: '5se0004h', token: 'v1' } })
  })
})
