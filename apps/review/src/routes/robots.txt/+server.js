// API answers are data for the interface; the crop images under /atlas/media stay open to image search.
// A run of characters has a page for every run in the corpus and none is indexed, so crawlers stay out.
export const GET = ({ url }) => new Response(`User-agent: *
Allow: /atlas/media/
Disallow: /atlas
Disallow: /layers
Disallow: /images/
Disallow: /reviews
Disallow: /api/auth
Disallow: /api/account
Disallow: /api/admin
Disallow: /api/favourites
Disallow: /sequence/
Disallow: /*/sequence/

Sitemap: ${url.origin}/sitemap.xml
`, { headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'public, max-age=3600' } })
