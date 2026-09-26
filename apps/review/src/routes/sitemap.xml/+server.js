import { families, formsAvailable } from '$lib/forms.js'

const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;')

// The pages worth arriving at from a search: the collection, and each character's forms. A single crop
// is reachable from those and is not listed one by one.
export async function GET({ fetch, url }) {
  const paths = ['/']
  if (await formsAvailable(fetch)) {
    paths.push('/forms', ...(await families({ fetch })).items.map(item => '/forms/' + item.code_point))
  }
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${paths.map(path => `  <url><loc>${escape(url.origin + path)}</loc></url>`).join('\n')}
</urlset>
`
  return new Response(body, { headers: { 'content-type': 'application/xml; charset=utf-8', 'cache-control': 'public, max-age=3600' } })
}
