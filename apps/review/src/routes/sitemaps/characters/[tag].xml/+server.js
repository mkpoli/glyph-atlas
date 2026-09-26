import { error } from '@sveltejs/kit'
import { isLocale } from '$lib/i18n.svelte.js'
import { characterPaths, urlset } from '$lib/sitemap.js'

// One language's character pages per file, so each stays well inside a sitemap's size limit.
export async function GET({ fetch, params, url }) {
  if (!isLocale(params.tag)) error(404, 'No such language.')
  return urlset(url.origin, await characterPaths(fetch), [params.tag])
}
