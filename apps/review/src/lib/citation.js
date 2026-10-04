import { t, formatDate } from './i18n.svelte.js'
import { licenceName } from './licence.js'

/** The day `now` falls on where the reader is, as a citation's access date. */
export const today = (now = new Date()) => [now.getFullYear(), now.getMonth() + 1, now.getDate()]

/**
 * A citation in the reader's language: what the entry is, the site, its address and the day it was
 * read, then for a crop where its image comes from, on what terms, and who transcribed it. The parts
 * are the record's own words; a part the record lacks is left out.
 */
export function citeText(entry, origin, now = new Date()) {
  const list = parts => parts.filter(Boolean).join(t('cite.list'))
  const what = entry.kind === 'crop'
    ? list([entry.label && t('cite.quote', { text: entry.label }), t('cite.crop', { id: entry.id }), entry.token && t('cite.version', { version: entry.token })])
    : list([entry.label ? t('cite.character', { char: entry.label, codePoint: entry.codePoint }) : entry.codePoint, entry.kind === 'grapheme' ? t('cite.grapheme') : t('cite.form')])
  const source = entry.source ?? {}
  const sentences = [t('cite.sentence', { entry: what, site: t('cite.site'), url: origin + entry.path, date: formatDate(now) })]
  const where = list([source.title, source.page && t('tile.page', { page: source.page }), source.holder, source.shelfmark])
  if (where) sentences.push(t('cite.source', { source: where }))
  const image = list([source.attribution, licenceName(source.licence) ?? source.licence])
  if (image) sentences.push(t('cite.image', { credit: image }))
  const transcription = list([source.transcription, source.transcriptionPage])
  if (transcription) sentences.push(t('cite.transcription', { credit: transcription }))
  return sentences.join(t('cite.space'))
}

