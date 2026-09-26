import { index } from '$lib/sitemap.js'

export const GET = ({ url }) => index(url.origin)
