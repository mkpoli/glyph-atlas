import { citedVersion, versionToken } from '../../../cloudflare/src/citation.ts'

/**
 * The cut of a crop an address's `?v=` cites: `current` when it is the crop's own cut now, else the
 * earlier version it names from the crop's recorded versions, or `version: null` when the crop has none
 * by that name. Null when the versions could not be read, so nothing is said about them.
 */
export async function citedCut(record, token, fetch) {
  if (versionToken(record.id, record.crop_version) === token) return { id: record.id, token, current: true, version: null }
  let versions
  try {
    const response = await fetch('/atlas/characters/' + encodeURIComponent(record.id) + '/versions')
    if (!response.ok) return null
    versions = (await response.json()).versions ?? []
  } catch { return null }
  const id = citedVersion(record.id, token, versions.map(version => version.id))
  return { id: record.id, token, current: false, version: versions.find(version => version.id === id) ?? null }
}
