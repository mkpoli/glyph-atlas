// What a crop's page tells search engines: its source, holder and licence, read from the record the
// page renders. Nothing is stated that the record does not carry.

const CREATIVE_COMMONS = /^CC-(BY(?:-NC)?(?:-SA|-ND)?)-(\d\.\d)$/

/** The deed of a Creative Commons or public-domain licence id, or null for any other licence. */
export function licenceUrl(licence) {
  if (licence === 'PDM-1.0') return 'https://creativecommons.org/publicdomain/mark/1.0/'
  if (licence === 'CC0-1.0') return 'https://creativecommons.org/publicdomain/zero/1.0/'
  const match = CREATIVE_COMMONS.exec(licence ?? '')
  return match ? `https://creativecommons.org/licenses/${match[1].toLowerCase()}/${match[2]}/` : null
}

/** A collection record names its source as text, a corpus record as an object with a title. */
export const sourceTitle = record => typeof record.source === 'string' ? record.source : record.source?.title ?? null
export const holderOf = record => record.holder ?? (typeof record.source === 'object' ? record.source?.holder : null) ?? null

/** The crop image as schema.org describes one: where it is, what it shows, and on what terms. */
export function cropImage(record, url) {
  const licence = licenceUrl(record.licence)
  const credit = record.attribution ?? holderOf(record)
  return {
    '@type': 'ImageObject',
    contentUrl: url,
    caption: [record.label, sourceTitle(record)].filter(Boolean).join(' · '),
    ...(licence ? { license: licence } : {}),
    ...(record.rights_url ? { acquireLicensePage: record.rights_url } : {}),
    ...(credit ? { creditText: credit } : {}),
  }
}
