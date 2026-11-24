import { describe, expect, it, mock } from 'bun:test'
import { plugin } from 'bun'
import { readFile } from 'node:fs/promises'
import { compile } from 'svelte/compiler'
import { render } from 'svelte/server'
import * as seo from '../src/lib/seo.js'
const { indexCollection } = seo
mock.module('$lib/seo.js', () => seo)
mock.module('$lib/citation.js', () => ({ galleryTitle: shown => shown.char }))

const LOCALES = ['en', 'ja', 'zh-Hant', 'lzh', 'ja-x-classical'].map(tag => ({ tag }))
const HREFLANG = LOCALES.slice(0, 3)
const localize = (path, tag) => `/${tag}${path === '/' ? '' : path}`
const delocalize = pathname => {
  const [, tag, ...rest] = pathname.split('/')
  return { tag, path: '/' + rest.join('/') }
}
const language = { LOCALES, HREFLANG, localize, delocalize, t: key => key, locale: () => delocalize(page.url.pathname).tag }
mock.module('../src/lib/i18n.svelte.js', () => language)
mock.module('$lib/i18n.svelte.js', () => language)
const page = { url: new URL('https://atlas.test/en') }
mock.module('$app/state', () => ({ page }))
mock.module('../src/lib/forms.js', () => ({ familyCodes: async () => ['U+304B'] }))
let counts = []
mock.module('../src/lib/client.js', () => ({ graphemeCounts: async () => counts }))
mock.module('../src/lib/issues.js', () => ({ hasCharacterPage: text => [...text].length === 1,
  pointOf: char => [...char].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ') }))
plugin({ name: 'seo-server-render', setup(build) {
  build.onLoad({ filter: /\.svelte$/ }, async ({ path }) => ({
    contents: compile(await readFile(path, 'utf8'), { filename: path, generate: 'server' }).js.code,
    loader: 'js',
  }))
} })
const { default: Seo } = await import('../src/components/Seo.svelte')
mock.module('$components/Seo.svelte', () => ({ default: Seo }))
mock.module('$views/Explore.svelte', () => ({ default: () => {} }))
mock.module('$lib/inspector.svelte.js', () => ({ useInspector: () => ({ inspect() {} }) }))
mock.module('$lib/session.svelte.js', () => ({ useSession: () => ({ state: { ink: 'original' }, setInk() {} }) }))
const { default: Collection } = await import('../src/routes/[lang=locale]/+page.svelte')
const { characterPaths, pagePaths, urlset } = await import('../src/lib/sitemap.js')
const { GET: robots } = await import('../src/routes/robots.txt/+server.js')

describe('search indexing', () => {
  it('indexes the collection and tracking links, excluding search and filter results', () => {
    for (const query of ['', 'utm_source=news', 'group=all', 'q=%20']) expect(indexCollection(new URLSearchParams(query))).toBe(true)
    for (const query of ['q=two+words', 'grapheme=U%2B304B', 'work=book', 'group=kana']) expect(indexCollection(new URLSearchParams(query))).toBe(false)
  })
  it('wires collection query parameters into rendered page metadata', () => {
    for (const [query, indexed] of [['?utm_source=news', true], ['?q=two+words', false]]) {
      page.url = new URL(`https://atlas.test/en${query}`)
      const { head } = render(Collection, { props: { data: { explore: {} } } })
      expect(head.includes('rel="canonical"')).toBe(indexed)
      expect(head.includes('noindex, follow')).toBe(!indexed)
    }
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
  it('names the page kept for a gallery that another page shows, in every language', () => {
    page.url = new URL('https://atlas.test/ja/grapheme/U+9047')
    const { head } = render(Seo, { props: { path: '/character/U+9047' } })
    for (const tag of ['en', 'ja', 'zh-Hant']) expect(head).toContain(`hreflang="${tag}" href="https://atlas.test/${tag}/character/U+9047"`)
    expect(head).toContain('rel="canonical" href="https://atlas.test/ja/character/U+9047"')
    expect(head).not.toContain('/grapheme/')
  })
  it('lists each character held, and each grapheme no one character holds every crop of', async () => {
    // 仮's second form is the corpus's, crops with no character name は, and a claim filed か under 加.
    counts = [['仮', 'U+4EEE', 1, 0], ['假', 'U+4EEE', 0, 2], ['遇', 'U+9047', 1, 1], ['は', 'U+306F', 3, 0], ['', 'U+306F', 0, 2], ['加', 'U+52A0', 1, 0],
      ['か', 'U+52A0', 1, 0, 1], ['か゚', 'U+304B U+309A', 1, 0], ['が゚', 'U+304B U+309A', 1, 0], ['債', 'U+50B5', 0, 4]]
    const paths = ['/character/U+304B', '/character/U+306F', '/character/U+4EEE', '/character/U+52A0', '/character/U+9047', '/grapheme/U+306F', '/grapheme/U+4EEE', '/grapheme/U+52A0']
    expect(await characterPaths(() => {})).toEqual(paths)
    counts = [...counts].reverse()
    expect(await characterPaths(() => {})).toEqual(paths)
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
    expect(paths).toEqual(['/', '/contact', '/privacy', '/ranking', '/roadmap', '/forms/U+304B'])
    const body = await urlset('https://atlas.test', paths, LOCALES.map(locale => locale.tag)).text()
    for (const tag of LOCALES.map(locale => locale.tag)) {
      for (const path of paths) expect(body).toContain(`<loc>https://atlas.test${localize(path, tag)}</loc>`)
    }
    expect(body).not.toContain('hreflang="lzh"')
    expect(body).not.toContain('/review</loc>')
  })
  it('keeps crawlers out of run pages in every language', async () => {
    const body = await robots({ url: new URL('https://atlas.test/robots.txt') }).text()
    expect(body).toContain('Disallow: /sequence/\n')
    expect(body).toContain('Disallow: /*/sequence/\n')
    expect(body).not.toContain('character')
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
