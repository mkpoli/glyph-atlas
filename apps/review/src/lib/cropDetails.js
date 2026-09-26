import { t } from './i18n.svelte.js'
import { productionLabel } from '../components/ProductionBadge.svelte'

/** What a record says about its own reliability, in one badge: withheld, machine or confirmed.
 *
 * The words come from the record's `repair` block — `withheld`, `reliable`, `verified`, `reason` —
 * and nothing is claimed that the record does not state: a machine alignment with no human review
 * reads `machine`, a withheld record reads `withheld`, and only `verified` reads `checked`.
 */
export function repairOf(item) {
  const repair = item.repair
  if (repair) {
    if (item.withheld || repair.withheld) return { kind: 'withheld', label: 'withheld', reason: repair.reason ?? t('repair.reason.heldBack') }
    if (repair.verified) return { kind: 'verified', label: 'checked', reason: repair.reason ?? t('repair.reason.personChecked') }
    if (repair.machine || repair.reliable === false) {
      return { kind: 'machine', label: 'machine', reason: repair.reason ?? t('repair.reason.machineProposed') }
    }
    return null
  }
  // The corpus states its own case: a lead nobody has confirmed is not a verified example.
  if (item.origin === 'corpus' || item.requires_review) return { kind: 'machine', label: 'unconfirmed', reason: item.licence_note ?? t('repair.reason.corpusUnconfirmed') }
  if (item.machine) return { kind: 'machine', label: 'machine', reason: t('repair.reason.machineProposed') }
  return null
}

/** What a crop's details list: the reading when it differs, the material, how far the record
 * has been checked, where it comes from, the work and page, and the holder. */
export function cropDetails(item) {
  const repair = repairOf(item)
  const state = item.state === 'checked' ? t('state.checked') : item.state === 'flagged' ? t('state.flagged') : item.state === 'hard' ? t('state.hard')
    : repair?.kind === 'withheld' ? t('tile.withheldReason', { reason: repair.reason }) : repair?.kind === 'verified' ? t('state.checked')
    : repair?.label === 'unconfirmed' ? t('tile.notYetConfirmed') : repair ? t('tile.machineAligned') : null
  const origin = item.origin === 'corpus' ? ((item.source?.corpus ?? item.corpus) === 'codh-full' ? t('tile.origin.codh') : t('tile.origin.corpus')) : null
  const work = typeof item.source === 'string' ? item.source : (item.source?.title ?? item.title)
  const page = item.page_number ? t('tile.page', { page: item.page_number }) : null
  return [item.reading && item.reading !== item.label ? item.reading : null, productionLabel(item), state, origin,
    [work, page].filter(Boolean).join(' · ') || null, typeof item.source === 'string' ? item.holder : (item.source?.holder ?? item.holder)]
    .filter(Boolean)
}
