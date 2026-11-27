import { PREFIX, SHARDS } from 'kanji-readings'

// A kanji's Japanese readings in hiragana, as Unihan's kJapanese gives them (`data/vocab/kanji-readings.json`),
// in the order of the kana. One static file holds the kanji of a code point modulo SHARDS.
const shards = new Map()
// A character with a variation selector or marks on it is read as its first code point.
const base = char => String.fromCodePoint(char.codePointAt(0))
const shardOf = char => char.codePointAt(0) % SHARDS
function read(n) {
  if (!shards.has(n)) shards.set(n, fetch(`${PREFIX}${n}.json`).then(response => { if (!response.ok) throw new Error(String(response.status)); return response.json() })
    // A file that could not be read is asked for again next time.
    .catch(() => { shards.delete(n); return {} }))
  return shards.get(n)
}
/** The readings of each character of `chars` that has some, by character. */
export async function kanjiReadings(chars) {
  const wanted = [...new Set(chars.filter(char => char && /^\p{Script=Han}/u.test(char)))]
  const found = await Promise.all(wanted.map(async char => [char, (await read(shardOf(char)))[base(char)] ?? []]))
  return new Map(found.filter(([, readings]) => readings.length))
}
