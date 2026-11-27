import { request } from './client.js'

/** The most characters read together, as the site takes them (`RUN_MAX`, `apps/cloudflare/src/ledger.ts`). */
export const RUN_MAX = 12

/** A line of text as the characters a reader picks: each with the marks and variation selectors on it. */
export const textParts = text => (text ?? '').normalize('NFC').match(/\P{M}\p{M}*/gu) ?? []

const readable = part => /[\p{L}\p{N}]/u.test(part) || /\p{Co}/u.test(part) || part === '〓'
const bare = part => [...part][0]
const composed = text => text?.normalize('NFC') ?? ''
const same = (part, label) => { const char = composed(label); return Boolean(char) && (part === char || bare(part) === char || part === bare(char)) }
// The characters a crop may stand as in its line: what it is labelled, what it was read as, what its source gives.
const charsOf = crop => [crop.label, crop.written_character, crop.source_label].filter(Boolean)

/** The published crops beside the one at `at` that the line's text agrees with, by place: each neighbour in
 *  reading order is the next character of the text that a reader can pick, and the first that is another
 *  character, or stands past one that is not published, ends its side. */
export function beside(parts, at, neighbours) {
  const found = new Map()
  for (const side of [1, -1]) {
    let place = at, steps = 0
    for (const item of neighbours.filter(one => Math.sign(one.offset) === side).sort((a, b) => Math.abs(a.offset) - Math.abs(b.offset))) {
      if (Math.abs(item.offset) !== ++steps) break
      do place += side; while (parts[place] !== undefined && !readable(parts[place]))
      if (parts[place] === undefined || !same(parts[place], item.label)) break
      found.set(place, item)
    }
  }
  return found
}

/**
 * Where a crop stands on its line: the line's characters, its place among them, and under `also` the
 * line's other places of its character. A character the line has several times is the one whose neighbours
 * agree most (`sure` says whether the place is known). A crop whose line does not hold its character (`held`) stands
 * among the labels of its neighbours, or alone.
 */
export function cropLine(crop, neighbours = []) {
  const parts = textParts(crop.text), chars = charsOf(crop)
  const places = parts.flatMap((part, i) => chars.some(char => same(part, char)) ? [i] : [])
  if (places.length) {
    const agreeing = places.map(place => beside(parts, place, neighbours).size)
    const most = Math.max(...agreeing), at = places[agreeing.indexOf(most)]
    // The place is sure where the line writes the character once, or one place alone agrees most with the neighbours.
    return { parts, at, also: places.filter(place => place !== at), held: true, sure: places.length === 1 || (most > 0 && agreeing.filter(n => n === most).length === 1) }
  }
  const line = neighbours.filter(one => one.offset === 0 || one.label).sort((a, b) => a.offset - b.offset)
  const here = line.findIndex(one => one.offset === 0), own = composed(crop.written_character ?? crop.label)
  return here < 0 ? { parts: [own], at: 0, also: [] } : { parts: line.map(one => one.offset === 0 ? own : composed(one.label)), at: here, also: [] }
}

/**
 * The ruby the page writes beside a crop's run, on the line its source gives: the line before and after the
 * run, and the ruby. Null while readers disagree on it, or where the line does not hold the run, or holds it twice.
 */
export function rubyLine(crop) {
  const slot = crop?.reading?.ruby, [value, ...others] = slot?.values ?? []
  if (!value || others.length || slot.status === 'disputed') return null
  const parts = textParts(crop.text)
  const starts = parts.flatMap((_, i) => value.run.every((part, j) => parts[i + j] === part) ? [i] : [])
  if (starts.length !== 1) return null
  return { before: parts.slice(0, starts[0]).join(''), value, after: parts.slice(starts[0] + value.run.length).join('') }
}

/**
 * The rubies a crop's transcription gives its line (`transcribed_ruby`: start and length over the line's
 * letters and numbers, side, and the reading over the base or over each character), placed on the line's
 * characters: `{ from, to, side, run, ruby }`. One whose base the line parts is left out.
 */
export function transcribedRubies(crop) {
  const placed = onLine(crop)
  return (crop?.transcribed_ruby ?? []).flatMap(([start, length, side, reading]) => {
    const span = placed(start, length)
    return span ? [{ ...span, side: side ? 'left' : 'right', ruby: [reading].flat() }] : []
  })
}
// A span over the line's letters and numbers, as the places of the line's characters; null where the line parts it.
function onLine(crop) {
  const parts = textParts(crop?.text), places = parts.flatMap((part, i) => /^[\p{L}\p{N}]/u.test(part) ? [i] : [])
  return (start, length) => {
    const from = places[start], to = places[start + length - 1]
    return !(length > 0) || from === undefined || to === undefined || to - from !== length - 1 ? null : { from, to, run: parts.slice(from, to + 1) }
  }
}

/**
 * The readings the pages around a crop's give a word of its line that stands here under no ruby
 * (`ruby_elsewhere`: start and length over the line's letters and numbers, the reading as the ruby there
 * gives it, the lines that give it and a page of one): `{ from, to, run, reading, lines, page }`.
 */
export function rubiesElsewhere(crop) {
  const placed = onLine(crop)
  return (crop?.ruby_elsewhere ?? []).flatMap(([start, length, reading, lines, page]) => {
    const span = placed(start, length)
    return span ? [{ ...span, reading: [reading].flat(), lines, page }] : []
  })
}
/** The readings given elsewhere to the run from `from` to `to` of the crop's line. */
export const readingsElsewhere = (crop, from, to) => rubiesElsewhere(crop).filter(one => one.from === from && one.to === to)
/** The longest word around the place `at` that is read elsewhere, of more than one character, or null. */
export function wordElsewhere(crop, at) {
  const [longest] = rubiesElsewhere(crop).filter(one => one.from <= at && at <= one.to && one.to > one.from).sort((a, b) => (b.to - b.from) - (a.to - a.from) || b.lines - a.lines)
  return longest ? { from: longest.from, to: longest.to } : null
}

/**
 * The transcription's rubies over the crop itself, as the entries a reading has: each run with its ruby and
 * 左訓, marked `transcribed`. None where the line writes the crop's character more than once, since nothing
 * says which place is the crop's until its neighbours are read (`at` names the place once they are).
 */
export function transcribedEntries(crop, at = null) {
  if (!crop?.transcribed_ruby?.length) return []
  const line = cropLine(crop)
  if (!line.held || (at === null && !line.sure)) return []
  const place = at ?? line.at, entries = []
  for (const ruby of transcribedRubies(crop)) {
    if (place < ruby.from || place > ruby.to) continue
    const value = { run: ruby.run, at: place - ruby.from, ruby: ruby.ruby }
    let entry = entries.find(one => together(one.run, value))
    if (!entry) entries.push(entry = { run: value, ruby: [], left: [], notes: [], transcribed: true })
    entry[ruby.side === 'left' ? 'left' : 'ruby'].push(value)
  }
  return entries
}

/**
 * A crop's line as it is shown: stretches of text, and runs under a ruby. A ruby readers claim stands first; the
 * transcription's right rubies fill the rest of the line, the outer of two that overlap. Null for a line with none.
 */
export function lineRubies(crop) {
  const parts = textParts(crop?.text), claimed = rubyLine(crop), spans = []
  if (claimed) { const from = textParts(claimed.before).length; spans.push({ from, to: from + claimed.value.run.length - 1, value: claimed.value }) }
  const given = transcribedRubies(crop)
  if (given.length) {
    const place = cropLine(crop), own = place.held && place.sure ? place.at : -1
    for (const ruby of given) {
      if (ruby.side !== 'right' || spans.some(span => ruby.from <= span.to && ruby.to >= span.from)) continue
      spans.push({ from: ruby.from, to: ruby.to, value: { run: ruby.run, at: own >= ruby.from && own <= ruby.to ? own - ruby.from : -1, ruby: ruby.ruby }, transcribed: true })
    }
  }
  if (!spans.length) return null
  const line = []
  let at = 0
  for (const span of spans.sort((a, b) => a.from - b.from)) {
    if (span.from > at) line.push({ text: parts.slice(at, span.from).join('') })
    line.push({ value: span.value, transcribed: Boolean(span.transcribed) })
    at = span.to + 1
  }
  if (at < parts.length) line.push({ text: parts.slice(at).join('') })
  return line
}

const together = (a, b) => a.at === b.at && JSON.stringify(a.run) === JSON.stringify(b.run)
/**
 * What the inspector shows of a crop's runs: each run with the rubies the page writes beside it, and the notes
 * readers make of how it is read.
 */
export function readingEntries(slot, crop = null) {
  const entries = []
  for (const [field, values] of [['ruby', slot?.ruby?.values], ['left', slot?.left?.values], ['notes', slot?.values]]) for (const value of values ?? []) {
    let entry = entries.find(one => together(one.run, value))
    if (!entry) entries.push(entry = { run: value, ruby: [], left: [], notes: [] })
    entry[field].push(value)
  }
  // What the transcription gives the crop stands until a reader says how the same run is written.
  if (crop) entries.push(...transcribedEntries(crop).filter(given => !entries.some(one => one.ruby.length && together(one.run, given.run))))
  return entries
}

/**
 * What the editor opens with, for a reader: the reading and the rubies they hold, or, holding none, the
 * ones nobody disputes. A ruby beside another run says nothing of this one. `value` names the run.
 */
export function opening(slot, user) {
  const mine = value => Boolean(user && value.by?.includes(user))
  const readings = slot?.values ?? [], rubies = slot?.ruby?.values ?? [], lefts = slot?.left?.values ?? []
  const holds = [...readings, ...rubies, ...lefts].some(mine)
  const from = (values, status) => holds ? values.find(mine) : status === 'disputed' ? undefined : values[0]
  const reading = from(readings, slot?.status), ruby = from(rubies, slot?.ruby?.status), left = from(lefts, slot?.left?.status)
  const value = reading ?? ruby ?? left ?? null, beside = one => value && one && together(value, one) ? one.ruby : []
  return { value, reading: reading?.reading ?? [], ruby: beside(ruby), left: beside(left), method: reading?.methods?.[0] ?? null }
}

/** What the editor opens with where no reader has said anything: the ruby and 左訓 the transcription gives the
 *  crop's place on its line (`at`), over their run, or null. */
export function openingTranscribed(crop, at = null) {
  const [entry] = transcribedEntries(crop, at)
  return entry ? { value: entry.run, reading: [], ruby: entry.ruby[0]?.ruby ?? [], left: entry.left[0]?.ruby ?? [], method: null } : null
}


/** The places a run around `at` can reach: the characters up to the punctuation either side of it. */
export function stretch(parts, at) {
  let from = at, to = at
  while (from > 0 && readable(parts[from - 1])) from--
  while (to < parts.length - 1 && readable(parts[to + 1])) to++
  return { from, to }
}

/** The run after a reader presses the character at `place`: it grows to reach a character outside it, and
 *  a character inside it, other than the crop's own, is left out with everything beyond it. */
export function pressed({ from, to }, at, place) {
  if (place < from) return { from: Math.max(place, to - RUN_MAX + 1), to }
  if (place > to) return { from, to: Math.min(place, from + RUN_MAX - 1) }
  return place < at ? { from: place + 1, to } : place > at ? { from, to: place - 1 } : { from, to }
}

// A lost answer keeps its submission id; an answered save of the same value is a new one.
const submissions = new Map()
async function send(crop, value) {
  const key = JSON.stringify([crop.id, crop.crop_version, value])
  if (!submissions.has(key)) submissions.set(key, crypto.randomUUID())
  let result
  try {
    result = await request(`/atlas/characters/${encodeURIComponent(crop.id)}/reading`,
      { id: submissions.get(key), crop_version: crop.crop_version, ...value })
  } catch (error) { if (error.status) submissions.delete(key); throw error }
  submissions.delete(key)
  return { ...crop, reading: result.reading, word: result.word }
}

/**
 * Save how a crop is read: `run` with the crop at `at`, the `reading` over each character or over all of
 * them, what it rests on and the ruby and the 左訓 (`left`) as the page writes them, or null. The published crops of the run (`crops`, by place in it)
 * are saved with it, each at the version it is shown at.
 * `word` ({ id, form, method }) names the dictionary word the run writes, null takes the reader's back, and without it the word the reader holds goes with the run.
 */
export function setReading(crop, { run, at, reading, ruby = null, left = null, method, word, crops = new Map() }) {
  const others = [...crops].map(([place, one]) => ({ id: one.id, crop_version: one.crop_version, at: place }))
  return send(crop, { run, at, reading, ruby, left, method, others, ...(word === undefined ? {} : { word }) })
}

/** Take back the reader's run on a crop and, with `word`, the word it writes. */
export const clearReading = (crop, { word = false } = {}) => send(crop, { run: null, ...(word ? { word: null } : {}) })
