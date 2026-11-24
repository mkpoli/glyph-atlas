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
 * agree most. A crop whose line does not hold its character stands among the labels of its neighbours, or alone.
 */
export function cropLine(crop, neighbours = []) {
  const parts = textParts(crop.text), chars = charsOf(crop)
  const places = parts.flatMap((part, i) => chars.some(char => same(part, char)) ? [i] : [])
  if (places.length) {
    const agreeing = places.map(place => beside(parts, place, neighbours).size)
    const at = places[agreeing.indexOf(Math.max(...agreeing))]
    return { parts, at, also: places.filter(place => place !== at) }
  }
  const line = neighbours.filter(one => one.offset === 0 || one.label).sort((a, b) => a.offset - b.offset)
  const here = line.findIndex(one => one.offset === 0), own = composed(crop.written_character ?? crop.label)
  return here < 0 ? { parts: [own], at: 0, also: [] } : { parts: line.map(one => one.offset === 0 ? own : composed(one.label)), at: here, also: [] }
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
  return { ...crop, reading: result.reading }
}

/**
 * Save how a crop is read: `run` with the crop at `at`, the `reading` over each character or over all of
 * them, what it rests on and the ruby as the page writes it, or null. The published crops of the run (`crops`, by place in it)
 * are saved with it, each at the version it is shown at.
 */
export function setReading(crop, { run, at, reading, ruby = null, method, crops = new Map() }) {
  const others = [...crops].map(([place, one]) => ({ id: one.id, crop_version: one.crop_version, at: place }))
  return send(crop, { run, at, reading, ruby, method, others })
}

export const clearReading = crop => send(crop, { run: null })
