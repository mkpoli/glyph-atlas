// Where a Matrix client signing in as `@name:glyphatlas.org` finds the homeserver. Web clients on other
// sites read it too.
export const GET = () => Response.json({ 'm.homeserver': { base_url: 'https://matrix.glyphatlas.org' } },
  { headers: { 'cache-control': 'public, max-age=3600', 'access-control-allow-origin': '*' } })
