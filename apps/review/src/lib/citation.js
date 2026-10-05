import { t, formatDate } from './i18n.svelte.js'
import { licenceName } from './licence.js'

/** The day `now` falls on where the reader is, as a citation's access date. */
export const today = (now = new Date()) => [now.getFullYear(), now.getMonth() + 1, now.getDate()]

/**
 * A citation in the reader's language, the way a reader writes one: what it shows and the document it
 * comes from, then the site, the day it was read and its address. For a crop the image's credit and
 * terms, and who transcribed it, follow as `credit`. The parts are the record's own words; a part the
 * record lacks is left out.
 */
export function citeText(entry, origin, now = new Date()) {
  const list = parts => parts.filter(Boolean).join(t('cite.list'))
  const source = entry.source ?? {}
  const label = entry.kind === 'crop'
    ? (entry.label ? (entry.form ? t('cite.label.cropForm', { char: entry.label, form: entry.form }) : t('cite.label.crop', { char: entry.label })) : t('cite.label.cropUnnamed'))
    : entry.kind === 'grapheme' ? t('cite.label.grapheme', { char: entry.label, codePoint: entry.codePoint })
    : t('cite.label.form', { char: entry.label, codePoint: entry.codePoint })
  // Page numbers are written as the source numbers them, without grouping.
  const page = source.page ? String(source.page) : null
  const work = source.title ? (page ? t('cite.workPage', { title: source.title, page }) : t('cite.work', { title: source.title }))
    : page ? t('cite.page', { page }) : null
  const what = list([label, work, source.holder && t('cite.holder', { holder: source.holder }), source.shelfmark]) + t('cite.end')
  const where = list([t('cite.site'), t('cite.accessed', { date: formatDate(now) }), origin + entry.path])
  const credits = [list([source.attribution, licenceName(source.licence) ?? source.licence]) || null, list([source.transcription, source.transcriptionPage]) || null]
  const credit = [credits[0] && t('cite.image', { credit: credits[0] }), credits[1] && t('cite.transcription', { credit: credits[1] })].filter(Boolean).join(t('cite.space'))
  return { citation: what + t('cite.space') + where, credit }
}
