import { families, formsAvailable } from '$lib/forms.js'
import { HREFLANG, LOCALES, localize } from '$lib/i18n.svelte.js'

const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;')

// The pages worth arriving at from a search: the collection, and each character's forms, in every
// interface language, each naming the others. A single crop is reachable from those and is not listed.
export async function GET({ fetch, url }) {
  const paths = ['/']
  if (await formsAvailable(fetch)) {
    paths.push(...(await families({ fetch })).items.map(item => '/forms/' + item.code_point))
  }
  const address = (path, tag) => escape(url.origin + localize(path, tag))
  const entry = path => {
    const links = [...HREFLANG.map(locale => `<xhtml:link rel="alternate" hreflang="${locale.tag}" href="${address(path, locale.tag)}"/>`),
      `<xhtml:link rel="alternate" hreflang="x-default" href="${address(path, 'en')}"/>`].join('')
    // A language without an hreflang code is listed on its own, outside the set that names each other.
    return LOCALES.map(locale => `  <url><loc>${address(path, locale.tag)}</loc>${HREFLANG.includes(locale) ? links : ''}</url>`).join('\n')
  }
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
${paths.map(entry).join('\n')}
</urlset>
`
  return new Response(body, { headers: { 'content-type': 'application/xml; charset=utf-8', 'cache-control': 'public, max-age=3600' } })
}
