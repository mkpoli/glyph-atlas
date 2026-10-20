import { citedVersion, versionToken } from '../../../cloudflare/src/citation.ts'

// A version id written with the crop's id before the cutover (`source_id`), as tokens issued then name it.
const issued = (record, id) => record.source_id && id?.startsWith(record.id + '@') ? record.source_id + id.slice(record.id.length) : null

/**
 * The cut of a crop an address's `?v=` cites: `current` when it is the crop's own cut now, else the
 * earlier version it names from the crop's recorded versions, or `version: null` when the crop has none
 * by that name. Null when the versions could not be read, so nothing is said about them. A token issued
 * before the cutover names the version by the crop's old id, and still finds it.
 */
export async function citedCut(record, token, fetch) {
  const current = [versionToken(record.id, record.crop_version), versionToken(record.source_id ?? '', issued(record, record.crop_version))]
  if (current.includes(token)) return { id: record.id, token, current: true, version: null }
  let versions
  try {
    const response = await fetch('/atlas/characters/' + encodeURIComponent(record.id) + '/versions')
    if (!response.ok) return null
    versions = (await response.json()).versions ?? []
  } catch { return null }
  const id = citedVersion(record.id, token, versions.map(version => version.id))
  const old = id ? null : citedVersion(record.source_id ?? '', token, versions.map(version => issued(record, version.id)).filter(Boolean))
  const version = versions.find(version => version.id === id || (old && issued(record, version.id) === old)) ?? null
  return { id: record.id, token, current: false, version }
}
