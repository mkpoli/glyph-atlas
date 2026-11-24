import { keyText } from './codePoints.js'

const codesOf = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
/** The text a grapheme key names, or the key itself where it names none. */
export const charOf = key => keyText(key) ?? key

/**
 * The characters the collection counts, gathered under their graphemes (仮 and 假 are one group, は, ハ
 * and 𛂞 one): `{ key, grapheme, char, count, local, corpus, members: [{ label, count }] }`. `categories`
 * are the catalogue's counts and `corpus` the corpus glyphs by character, `[label, grapheme, count]`.
 * Under a script (`inScript`) a group holds that script's characters. A grapheme headed by another
 * script's character gathers none of them: each is a group of its own, keyed by its own code points
 * (`alone`), so the small seal script lists small seal characters and no kanji heads them.
 */
export function gatherGraphemes({ categories, corpus = [], inScript = () => true, flagged = false }) {
  const groups = new Map()
  const groupFor = (grapheme, label) => {
    const alone = !inScript(charOf(grapheme)), key = alone ? codesOf(label) : grapheme
    const group = groups.get(key) ?? { key, grapheme, alone, char: charOf(key), count: 0, local: 0, corpus: 0, members: [] }
    groups.set(key, group)
    return group
  }
  for (const c of categories) {
    if (!inScript(c.label)) continue
    const group = groupFor(c.grapheme ?? c.label, c.label), count = flagged ? c.flagged + c.hard : c.total
    group.members.push({ label: c.label, count }); group.count += count; group.local += count
  }
  for (const [label, grapheme, n] of corpus) {
    if (!inScript(label)) continue
    const group = groupFor(grapheme, label), member = group.members.find(m => m.label === label)
    if (member) member.count += n; else group.members.push({ label, count: n })
    group.count += n; group.corpus += n
  }
  for (const group of groups.values()) group.members.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
  return [...groups.values()]
}

/**
 * What a chosen tile opens: the gallery of a tile that counts corpus glyphs, which the collection's own
 * listing leaves out (`corpus`, whether the tiles count them), a grapheme's whole family or, `alone`, its
 * one character; any other narrows the listing to the tile's own key, keeping the listing's filters.
 */
export const tileOpens = (group, corpus) =>
  corpus && group.corpus > 0 ? { gallery: group.key, scope: group.alone ? 'exact' : 'family' } : { narrow: group.key }
