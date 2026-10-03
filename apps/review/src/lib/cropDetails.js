import { t, withText } from './i18n.svelte.js'
import { productionLabel } from '../components/ProductionBadge.svelte'
import { formNames } from './cropForms.js'
import { datingLine } from './dating.js'

/** What a record says about its own reliability, in one badge: withheld, machine, outside the classifier's classes, or confirmed.
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
  // Extraction published this crop although the atlas classifier has no class for its character.
  if (item.gate === 'unconfirmed') return { kind: 'machine', label: 'no-class', reason: t('repair.reason.noClass') }
  // The corpus states its own case: a lead nobody has confirmed is not a verified example.
  if (item.origin === 'corpus' || item.requires_review) return { kind: 'machine', label: 'unconfirmed', reason: item.licence_note ?? t('repair.reason.corpusUnconfirmed') }
  if (item.machine) return { kind: 'machine', label: 'machine', reason: t('repair.reason.machineProposed') }
  return null
}

/** What a crop's details list: the written form, the material, how far the record
 * has been checked, where it comes from, the work and page, when the copy and its text were made, and the holder. A line that names a character is
 * `{ before, text, after }`, for `ScriptLine` to draw the character in its script's colour; the others are text. */
export function cropDetails(item) {
  const repair = repairOf(item)
  const state = item.state === 'checked' ? t('state.checked') : item.state === 'flagged' ? t('state.flagged') : item.state === 'hard' ? t('state.hard')
    : repair?.kind === 'withheld' ? t('tile.withheldReason', { reason: repair.reason }) : repair?.kind === 'verified' ? t('state.checked')
    : repair?.label === 'unconfirmed' ? t('tile.notYetConfirmed') : repair?.label === 'no-class' ? t('tile.noClass')
    : repair ? t('tile.machineAligned') : null
  const origin = item.origin === 'corpus' ? ((item.source?.corpus ?? item.corpus) === 'codh-full' ? t('tile.origin.codh') : t('tile.origin.corpus')) : null
  const work = typeof item.source === 'string' ? item.source : (item.source?.title ?? item.title)
  const page = item.page_number ? t('tile.page', { page: item.page_number }) : null
  // The form the site holds for the crop, when it is other than the label: the shape the letterforms
  // take, the crop still filed under its label. Competing forms are named together.
  const names = formNames(item.form)
  const written = names.length && names.join(' / ') !== item.label ? withText('written.detail', 'form', { form: names.join(' / ') }) : null
  return [written, productionLabel(item), state, origin,
    [work, page].filter(Boolean).join(' · ') || null, datingLine(item), typeof item.source === 'string' ? item.holder : (item.source?.holder ?? item.holder)]
    .filter(Boolean)
}
