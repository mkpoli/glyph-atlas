import { isSeal } from './cardTiers.js'
import { keyText } from './codePoints.js'

const codesOf = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
/** The text a grapheme key names, or the key itself where it names none. */
export const charOf = key => keyText(key) ?? key

/**
 * The characters the collection counts, gathered under their graphemes (仮 and 假 are one group, は, ハ
 * and 𛂞 one): `{ key, grapheme, char, count, local, corpus, members: [{ label, count }] }`. `categories`
 * are the catalogue's counts and `corpus` the corpus glyphs by character, `[label, grapheme, count]`.
 * Under a script (`inScript`) a group holds that script's characters. A small seal character filed under
 * another script's grapheme is a group of its own there, keyed by its own code points (`alone`); those of
 * its crops that claims file elsewhere stay with the grapheme claimed, as any character's do.
 */
export function gatherGraphemes({ categories, corpus = [], inScript = () => true, flagged = false }) {
  const rows = [...categories.map(c => [c.label, c.grapheme ?? c.label, flagged ? c.flagged + c.hard : c.total, 'local', c.total]),
    ...corpus.map(row => [...row, 'corpus', row[2]])].filter(([label]) => inScript(label))
  // A seal character's own grapheme: the one most of its glyphs are filed under.
  const sums = new Map(), own = new Map()
  for (const [label, grapheme, , , total] of rows) if (isSeal(label)) {
    const at = label + grapheme, sum = (sums.get(at) ?? 0) + total
    sums.set(at, sum)
    if (!own.has(label) || sum > sums.get(label + own.get(label))) own.set(label, grapheme)
  }
  const groups = new Map()
  for (const [label, grapheme, n, side] of rows) {
    const alone = own.get(label) === grapheme && !inScript(charOf(grapheme)), key = alone ? codesOf(label) : grapheme
    const group = groups.get(key) ?? { key, grapheme, alone, char: charOf(key), count: 0, local: 0, corpus: 0, members: [] }
    groups.set(key, group)
    const member = group.members.find(m => m.label === label)
    if (member) member.count += n; else group.members.push({ label, count: n })
    group.count += n; group[side] += n
  }
  for (const group of groups.values()) group.members.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
  return [...groups.values()]
}

/** The grapheme a corpus row is filed under, as the catalogue files a label. */
const rowGrapheme = row => row.grapheme?.code_point ?? row.grapheme ?? codesOf(row.label ?? '')
/** Whether a corpus row is one a key names: its grapheme's, or, under a member's key, its character's. */
export const rowNamed = (row, key) => rowGrapheme(row) === key || codesOf(row.label ?? '') === key

/**
 * What a chosen tile opens: the gallery of a tile that counts corpus glyphs, which the collection's own
 * listing leaves out (`corpus`, whether the tiles count them), a grapheme's whole family or, `alone`, its
 * one character; any other narrows the listing to the tile's own key, keeping the listing's filters.
 */
export const tileOpens = (group, corpus) =>
  corpus && group.corpus > 0 ? { gallery: group.key, scope: group.alone ? 'exact' : 'family' } : { narrow: group.key }
