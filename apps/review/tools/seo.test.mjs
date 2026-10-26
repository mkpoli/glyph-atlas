import { describe, expect, it, mock } from 'bun:test'
import { plugin } from 'bun'
import { readFile } from 'node:fs/promises'
import { compile } from 'svelte/compiler'
import { render } from 'svelte/server'
import { indexCollection } from '../src/lib/seo.js'

const LOCALES = ['en', 'ja', 'zh-Hant', 'lzh', 'ja-x-classical'].map(tag => ({ tag }))
const HREFLANG = LOCALES.slice(0, 3)
const localize = (path, tag) => `/${tag}${path === '/' ? '' : path}`
const delocalize = pathname => {
  const [, tag, ...rest] = pathname.split('/')
  return { tag, path: '/' + rest.join('/') }
}
const language = { LOCALES, HREFLANG, localize, delocalize, t: key => key }
mock.module('../src/lib/i18n.svelte.js', () => language)
mock.module('$lib/i18n.svelte.js', () => language)
const page = { url: new URL('https://atlas.test/en') }
mock.module('$app/state', () => ({ page }))
mock.module('../src/lib/forms.js', () => ({ familyCodes: async () => ['U+304B'] }))
mock.module('../src/lib/client.js', () => ({ catalogue: async () => ({ categories: [] }) }))
mock.module('../src/lib/issues.js', () => ({ hasCharacterPage: () => true, pointOf: value => value }))
plugin({ name: 'seo-server-render', setup(build) {
  build.onLoad({ filter: /Seo\.svelte$/ }, async ({ path }) => ({
    contents: compile(await readFile(path, 'utf8'), { filename: path, generate: 'server' }).js.code,
    loader: 'js',
  }))
} })
const { default: Seo } = await import('../src/components/Seo.svelte')
const { pagePaths, urlset } = await import('../src/lib/sitemap.js')
const { GET: robots } = await import('../src/routes/robots.txt/+server.js')

describe('search indexing', () => {
  it('indexes the collection and tracking links, excluding search and filter results', () => {
    for (const query of ['', 'utm_source=news', 'group=all', 'q=%20']) expect(indexCollection(new URLSearchParams(query))).toBe(true)
    for (const query of ['q=two+words', 'grapheme=U%2B304B', 'work=book', 'group=kana']) expect(indexCollection(new URLSearchParams(query))).toBe(false)
  })
  it('renders canonical and reciprocal language links without query parameters', () => {
    page.url = new URL('https://atlas.test/ja/character/U+304B?order=year')
    const { head } = render(Seo)
    expect(head).toContain('rel="canonical" href="https://atlas.test/ja/character/U+304B"')
    expect(head).toContain('hreflang="ja"')
    expect(head).toContain('hreflang="en"')
    expect(head).toContain('hreflang="x-default"')
    expect(head).not.toContain('?order=')
  })
  it('keeps unsupported hreflang languages indexable outside the alternate cluster', () => {
    for (const tag of ['lzh', 'ja-x-classical']) {
      page.url = new URL(`https://atlas.test/${tag}`)
      const { head } = render(Seo)
      expect(head).toContain(`rel="canonical" href="https://atlas.test/${tag}"`)
      expect(head).not.toContain('hreflang=')
      expect(head).not.toContain('noindex')
    }
  })
  it('renders noindex without conflicting canonical or alternate links', () => {
    page.url = new URL('https://atlas.test/en?q=two+words')
    const { head } = render(Seo, { props: { index: indexCollection(page.url.searchParams) } })
    expect(head).toContain('content="noindex, follow"')
    expect(head).not.toContain('rel="canonical"')
    expect(head).not.toContain('hreflang=')
  })
  it('lists public landing pages and form families in each language', async () => {
    const paths = await pagePaths(() => {})
    expect(paths).toEqual(['/', '/contact', '/privacy', '/ranking', '/forms/U+304B'])
    const body = await urlset('https://atlas.test', paths, LOCALES.map(locale => locale.tag)).text()
    for (const tag of LOCALES.map(locale => locale.tag)) {
      for (const path of paths) expect(body).toContain(`<loc>https://atlas.test${localize(path, tag)}</loc>`)
    }
    expect(body).not.toContain('hreflang="lzh"')
    expect(body).not.toContain('/review</loc>')
  })
  it('keeps crop images crawlable while excluding the account API', async () => {
    const body = await robots({ url: new URL('https://atlas.test/robots.txt') }).text()
    expect(body).toContain('Allow: /atlas/media/')
    for (const path of ['auth', 'account', 'admin', 'favourites']) expect(body).toContain(`Disallow: /api/${path}`)
    const blocked = body.split('\n').filter(line => line.startsWith('Disallow: ')).map(line => line.slice(10))
    for (const path of ['/api/ranking', '/api/community', '/api/avatars/example']) {
      expect(blocked.some(prefix => path.startsWith(prefix))).toBe(false)
    }
    expect(body).toContain('Sitemap: https://atlas.test/sitemap.xml')
  })
})
