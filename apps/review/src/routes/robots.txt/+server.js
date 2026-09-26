// API answers are data for the interface; the crop images under /atlas/media stay open to image search.
export const GET = ({ url }) => new Response(`User-agent: *
Allow: /atlas/media/
Disallow: /atlas
Disallow: /layers
Disallow: /images/
Disallow: /reviews

Sitemap: ${url.origin}/sitemap.xml
`, { headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'public, max-age=3600' } })
