import { request } from './client.js'
import { slug } from './gallery.js'

// A character's crops along its time axis (編年): the address, and the Worker's decades with a sample of
// each (`/layers/chronology`).

/** The address of a character's time axis, with what it is placed by and narrowed to. */
export function chronologyAddress(codePoint, { scope = '', axis = '', style = '', production = '', script = '' } = {}) {
  const query = new URLSearchParams(Object.entries({ scope, axis, style, production, script }).filter(([, value]) => value))
  return '/chronology/' + slug(codePoint) + (query.size ? '?' + query : '')
}

/** The productions the axis can be narrowed to: a top node of `data/vocab/production.yaml` each. */
export const PRODUCTIONS = ['handwritten', 'printed', 'inscribed']

/** What an address asks for, each value one the view knows or ''. */
export function chronologyOptions(params) {
  const pick = (key, allowed) => allowed.includes(params.get(key)) ? params.get(key) : ''
  return { scope: pick('scope', ['grapheme']), axis: pick('axis', ['composed']), style: pick('style', ['cursive', 'unassessed', 'formal']),
    production: pick('production', PRODUCTIONS), script: /^[a-z_-]{1,32}$/.test(params.get('script') ?? '') ? params.get('script') : '' }
}

/** The decades of a character's crops, `chars` naming the grapheme's characters a script narrows it to. */
export const chronology = (codePoint, { scope = '', axis = '', style = '', production = '', chars = [] } = {}, options = {}) => {
  const query = new URLSearchParams(Object.entries({ code_point: codePoint, scope: scope || 'character', axis: axis || 'witness', style, production,
    chars: chars.join(',') }).filter(([, value]) => value))
  return request('/layers/chronology?' + query, undefined, options)
}
