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
  // Each row's crops by side, and all the crops it stands for, whichever the list counts.
  const rows = counts && !flagged ? counts.map(([label, grapheme, local, corpus]) => ({ label, grapheme, local, corpus, all: local + corpus }))
    : categories.map(c => ({ label: c.label, grapheme: c.grapheme ?? c.label, local: flagged ? c.flagged + c.hard : c.total, corpus: 0, all: c.total }))
  const totals = new Map(), sums = new Map(), own = new Map()
  for (const { label, grapheme, local, corpus, all } of rows) {
    totals.set(grapheme, (totals.get(grapheme) ?? 0) + local + corpus)
    // A seal character's own grapheme: the one most of its glyphs are filed under.
    if (!isSeal(label)) continue
    const at = label + grapheme, sum = (sums.get(at) ?? 0) + all
    sums.set(at, sum)
    const held = own.get(label)
    if (held === undefined || sum > sums.get(label + held) || sum === sums.get(label + held) && grapheme < held) own.set(label, grapheme)
  }
  const groups = new Map()
  for (const { label, grapheme, local, corpus } of rows) {
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

// Of two graphemes with as many of a form's crops the lower key is taken, in whatever order the rows come.
const more = (sums, key, than) => sums.get(key) > sums.get(than) || sums.get(key) === sums.get(than) && key < than
/**
 * The crops of each written form and of each grapheme, from the counts' rows: `forms` and `groups` their
 * totals, `shapes` how many forms each grapheme is written with and `keys` the grapheme each form counts
 * under. A form one source files under another grapheme than the other (扵 under 於, or under its own),
 * or some of whose glyphs a claim reads as another grapheme, counts among the forms of each, and keys the
 * grapheme most of its glyphs are filed under. The crops with no character are the grapheme's alone.
 */
export function cropTotals(rows) {
  const forms = new Map(), groups = new Map(), keys = new Map(), shapes = new Map(), under = new Map()
  for (const [label, key, collected, corpus] of rows) {
    const n = collected + corpus
    groups.set(key, (groups.get(key) ?? 0) + n)
    if (!label) continue
    forms.set(label, (forms.get(label) ?? 0) + n)
    const sums = under.get(label) ?? new Map()
    if (!sums.has(key)) shapes.set(key, (shapes.get(key) ?? 0) + 1)
    sums.set(key, (sums.get(key) ?? 0) + n); under.set(label, sums)
    if (!keys.has(label) || more(sums, key, keys.get(label))) keys.set(label, key)
  }
  return { forms, groups, keys, shapes }
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
