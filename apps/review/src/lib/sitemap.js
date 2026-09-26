import { HREFLANG, LOCALES, localize } from './i18n.svelte.js'
import { families, formsAvailable } from './forms.js'
import { catalogue } from './client.js'

const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;')
const xml = body => new Response(`<?xml version="1.0" encoding="UTF-8"?>\n${body}\n`, {
  headers: { 'content-type': 'application/xml; charset=utf-8', 'cache-control': 'public, max-age=86400' },
})

/** The sitemap files the index names: the collection's own pages, and each language's character pages. */
export const index = origin => xml(`<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${['/sitemaps/pages.xml', ...LOCALES.map(locale => `/sitemaps/characters/${locale.tag}.xml`)]
  .map(path => `  <sitemap><loc>${escape(origin + path)}</loc></sitemap>`).join('\n')}
</sitemapindex>`)

/**
 * `paths` in the languages `tags`, each entry naming the page in every language search engines have a
 * code for. A language without such a code is listed on its own, outside that set.
 */
export function urlset(origin, paths, tags) {
  const address = (path, tag) => escape(origin + localize(path, tag))
  const entry = path => {
    const links = [...HREFLANG.map(locale => `<xhtml:link rel="alternate" hreflang="${locale.tag}" href="${address(path, locale.tag)}"/>`),
      `<xhtml:link rel="alternate" hreflang="x-default" href="${escape(origin + path)}"/>`].join('')
    return tags.map(tag => `  <url><loc>${address(path, tag)}</loc>${HREFLANG.some(locale => locale.tag === tag) ? links : ''}</url>`).join('\n')
  }
  return xml(`<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
${paths.map(entry).join('\n')}
</urlset>`)
}

/** The collection and each character's forms. */
export async function pagePaths(fetch) {
  const paths = ['/']
  if (await formsAvailable(fetch)) paths.push(...(await families({ fetch })).items.map(item => '/forms/' + item.code_point))
  return paths
}

/** A page for every single character the collection holds crops of. */
export async function characterPaths(fetch) {
  const { categories } = await catalogue({ limit: 1 }, { fetch })
  return categories.filter(category => category.total > 0 && [...category.label].length === 1)
    .map(category => '/character/U+' + category.label.codePointAt(0).toString(16).toUpperCase().padStart(4, '0'))
}
