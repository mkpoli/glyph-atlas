import { env } from '$env/dynamic/private'
import worker from '../../cloudflare/src/index.ts'
import { LOCALES, LOCALE_COOKIE, isLocale, localize, negotiate } from '$lib/i18n.svelte.js'

/** Headers that describe one connection, not the request, and are not passed on. */
const HOP = ['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade']

/** The paths the API owns; every other path is a page. */
const API = /^\/(atlas|layers|images|reviews)(\/|$)|^\/health$/

/**
 * Answer an API path. `ATLAS_REVIEW_API` names the local review service, which owns the journal and
 * the page photos; without it the request goes to the Worker's own API over its D1 and R2 bindings.
 * Pages fetch through here during server rendering too, so neither backend is ever called over the
 * network from the page server itself on Cloudflare.
 */
async function api(event) {
  const upstream = env.ATLAS_REVIEW_API
  if (!upstream) return worker.fetch(event.request, event.platform.env, event.platform.ctx)
  const headers = new Headers(event.request.headers)
  // The page server's fetch asks for an encoding it can decode; passing the browser's own list on can
  // bring back one it cannot (zstd), whose bytes would then reach the browser labelled as plain.
  for (const name of ['host', 'accept-encoding', ...HOP]) headers.delete(name)
  const body = ['GET', 'HEAD'].includes(event.request.method) ? undefined : event.request.body
  let response
  try {
    response = await fetch(new URL(event.url.pathname + event.url.search, upstream), {
      method: event.request.method, headers, body, duplex: 'half', redirect: 'manual',
    })
  } catch {
    return Response.json({ detail: 'The review service did not answer.' }, { status: 502 })
  }
  // The body arrives decoded, so the encoding and length it was sent with no longer describe it.
  const out = new Headers(response.headers)
  for (const name of ['content-encoding', 'content-length', ...HOP]) out.delete(name)
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers: out })
}

/** The files served beside the pages, which have no language. */
const UNLOCALIZED = /^\/(robots\.txt$|sitemap\.xml$|sitemaps\/)/

/** The language a reader wants: the one they chose, else the first their browser asks for that exists. */
function preferred(event) {
  const chosen = event.cookies.get(LOCALE_COOKIE)
  if (chosen && isLocale(chosen)) return chosen
  const wanted = (event.request.headers.get('accept-language') ?? '').split(',')
    .map(part => { const [tag, ...rest] = part.trim().split(';'); const q = rest.find(p => p.trim().startsWith('q='))
      return { tag, q: q ? Number(q.trim().slice(2)) : 1 } })
    .filter(entry => entry.tag && entry.tag !== '*' && entry.q > 0)
    .sort((a, b) => b.q - a.q)
    .map(entry => entry.tag)
  return negotiate(wanted)
}

/** A first path segment shaped like a language tag: fr, pt-BR, zh-TW, ja-x-classical. */
const LANGUAGE_TAG = /^[a-z]{2,3}(-[a-z0-9]{1,8})*$/i

const moved = (status, location) => new Response(null, { status, headers: { location, vary: 'Accept-Language, Cookie' } })

export async function handle({ event, resolve }) {
  const { pathname, search } = event.url
  if (API.test(pathname)) return api(event)
  if (UNLOCALIZED.test(pathname)) return resolve(event)
  const [, first, ...rest] = pathname.split('/')
  if (isLocale(first)) {
    event.locals.locale = first
    return resolve(event, { transformPageChunk: ({ html }) => html.replace('%lang%', first) })
  }
  // Every other address is sent to a language's address: a site language written in other case to that
  // language, and an unknown language or an unprefixed address to the page in the reader's language.
  // Leading slashes are collapsed so the target stays on this site (`//example.com` would leave it).
  const known = LANGUAGE_TAG.test(first) && LOCALES.find(locale => locale.tag.toLowerCase() === first.toLowerCase())
  const page = '/' + (LANGUAGE_TAG.test(first) ? rest : [first, ...rest]).join('/').replace(/^[/\\]+/, '')
  return moved(302, localize(page, known ? known.tag : preferred(event)) + search)
}
