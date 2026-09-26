import { env } from '$env/dynamic/private'
import worker from '../../cloudflare/src/index.ts'
import { LOCALE_COOKIE, isLocale, negotiate } from '$lib/i18n.svelte.js'

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
  headers.delete('host')
  const body = ['GET', 'HEAD'].includes(event.request.method) ? undefined : event.request.body
  const response = await fetch(new URL(event.url.pathname + event.url.search, upstream), {
    method: event.request.method, headers, body, duplex: 'half', redirect: 'manual',
  })
  // The body arrives decoded, so the encoding and length it was sent with no longer describe it.
  const out = new Headers(response.headers)
  out.delete('content-encoding'); out.delete('content-length')
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers: out })
}

/** The reader's language: the one they chose, else the first their browser asks for that exists. */
function language(event) {
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

export async function handle({ event, resolve }) {
  if (API.test(event.url.pathname)) return api(event)
  event.locals.locale = language(event)
  const response = await resolve(event, {
    transformPageChunk: ({ html }) => html.replace('%lang%', event.locals.locale),
  })
  // The same address renders in the reader's language.
  response.headers.append('vary', 'Accept-Language, Cookie')
  return response
}
