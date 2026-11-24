import { isSeal } from './cardTiers.js'
import { keyText } from './codePoints.js'

const codesOf = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
/** The text a grapheme key names, or the key itself where it names none. */
export const charOf = key => keyText(key) ?? key

/**
 * The graphemes the site holds crops of, each with the characters it is written with (仮 and 假 are one
 * group, は, ハ and 𛂞 one): `{ key, grapheme, char, count, local, corpus, members: [{ label, count }] }`.
 * `counts` are every crop by character and grapheme, `[label, grapheme, collected, corpus]`, '' for the
 * crops with no character; until they are read, `categories`, the catalogue's, stand in. A group's `count`
 * is its grapheme's total, whatever narrows the list. In the Flagged view the groups and their counts
 * are of the crops waiting for a person, and with `collected` the groups are those the collection holds
 * crops of. Under a script (`inScript`) a group lists that script's characters. A small seal character
 * filed under another script's grapheme is a group of its own there, keyed by its own code points
 * (`alone`); those of its crops that claims file elsewhere stay with the grapheme claimed, as any
 * character's do.
 */
export function gatherGraphemes({ categories = [], counts = null, inScript = () => true, flagged = false, collected = false }) {
  const rows = counts && !flagged ? counts
    : categories.map(c => [c.label, c.grapheme ?? c.label, flagged ? c.flagged + c.hard : c.total, 0, c.total])
  const totals = new Map(), sums = new Map(), own = new Map()
  for (const [label, grapheme, local, corpus, all = local + corpus] of rows) {
    totals.set(grapheme, (totals.get(grapheme) ?? 0) + local + corpus)
    // A seal character's own grapheme: the one most of its glyphs are filed under.
    if (!isSeal(label)) continue
    const at = label + grapheme, sum = (sums.get(at) ?? 0) + all
    sums.set(at, sum)
    if (!own.has(label) || sum > sums.get(label + own.get(label))) own.set(label, grapheme)
  }
  const groups = new Map()
  for (const [label, grapheme, local, corpus] of rows) {
    if (!inScript(label || charOf(grapheme))) continue
    const alone = own.get(label) === grapheme && !inScript(charOf(grapheme)), key = alone ? codesOf(label) : grapheme
    const group = groups.get(key) ?? { key, grapheme, alone, char: charOf(key), count: 0, local: 0, corpus: 0, members: [] }
    groups.set(key, group)
    const member = group.members.find(m => m.label === label), n = local + corpus
    if (member) member.count += n; else if (label) group.members.push({ label, count: n })
    group.count += n; group.local += local; group.corpus += corpus
  }
  for (const group of groups.values()) {
    if (!group.alone) group.count = totals.get(group.grapheme)
    if (!group.members.length) group.members.push({ label: group.char, count: 0 })
    group.members.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
  }
  return collected ? [...groups.values()].filter(group => group.local > 0) : [...groups.values()]
}

/** The grapheme a corpus row is filed under, as the catalogue files a label. */
const rowGrapheme = row => row.grapheme?.code_point ?? row.grapheme ?? codesOf(row.label ?? '')
/** Whether a corpus row is one a key names: its grapheme's, or, under a member's key, its character's. */
export const rowNamed = (row, key) => rowGrapheme(row) === key || codesOf(row.label ?? '') === key

/**
 * What a chosen tile opens: the gallery of a tile that counts corpus glyphs, which the collection's own
 * listing leaves out (`corpus`, whether the tiles count them), the grapheme's or, `alone`, its one
 * character's (`of`); any other narrows the listing to the tile's own key, keeping the listing's filters.
 */
export const tileOpens = (group, corpus) =>
  corpus && group.corpus > 0 ? { gallery: group.key, of: group.alone ? 'character' : 'grapheme' } : { narrow: group.key }
