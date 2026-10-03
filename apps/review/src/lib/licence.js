import { t } from './i18n.svelte.js'

// The name a reader sees for a licence id the records carry (data/vocab/licences.yaml), never the id.
// Creative Commons and the other standard licences go by their short names, which are the same in
// every language; the rest are named in the reader's language. An id with no name says nothing.
const NAMES = { 'CC0-1.0': 'CC0 1.0', 'KOGL-1': 'KOGL Type 1', 'Unicode-3.0': 'Unicode License v3', 'Apache-2.0': 'Apache 2.0' }
const OWN = { PD: () => t('licence.pd'), 'PDM-1.0': () => t('licence.pdm'), 'bespoke-free': () => t('licence.bespoke'),
  'RS-NOC-CR': () => t('licence.rsNocCr'), restricted: () => t('licence.restricted') }

export function licenceName(id) {
  if (!id || id === 'unknown') return null
  if (NAMES[id]) return NAMES[id]
  if (OWN[id]) return OWN[id]()
  const cc = /^CC-(BY(?:-(?:NC|ND|SA))*)-(\d\.\d)(?:-([A-Z]{2}))?$/.exec(id)
  if (cc) return `CC ${cc[1]} ${cc[2]}${cc[3] ? ' ' + cc[3] : ''}`
  return null
}

/** A holder as a reader sees it: the source's name, without the editorial note a statement's label carries. */
export const holderName = text => (text ?? '').replace(/\s*[(（][^()（）]*[)）]\s*$/, '').trim() || null
