import { LOCALES } from '$lib/i18n.svelte.js'
import { pagePaths, urlset } from '$lib/sitemap.js'

export const GET = async ({ fetch, url }) => urlset(url.origin, await pagePaths(fetch), LOCALES.map(locale => locale.tag))
