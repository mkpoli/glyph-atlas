// Where other Matrix servers reach the community's homeserver for accounts named `@name:glyphatlas.org`.
export const GET = () => Response.json({ 'm.server': 'matrix.glyphatlas.org:443' }, { headers: { 'cache-control': 'public, max-age=3600' } })
