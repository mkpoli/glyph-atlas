/**
 * The query an old crop address keeps on its way to the crop's public one. A citation's `?v=` names a
 * version of the crop the address named: it is kept for the crop's own old id or another spelling of its
 * public id, and dropped for a retired crop's, whose versions are not its replacement's.
 */
export function movedQuery(record, asked, search) {
  const kept = new URLSearchParams(search)
  if (record.source_id !== asked && record.id !== asked.toLowerCase()) kept.delete('v')
  return kept.size ? '?' + kept : ''
}
