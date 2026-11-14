/** A character card's 異体字 graph in three levels: the 字種 (the grapheme, and any other grapheme this
 *  form also writes, 后: 後), its 字体 (the grapheme's own forms, then the 異体字 outside it, the ones a
 *  gallery widens to first, then the described forms reviewers said are written for it), and the
 *  characters related otherwise (a loan, a homograph, a fallback reduction). A form that writes this
 *  grapheme and a character of its own (後: 后) is a 字体 tagged as shared. A card cached before rows
 *  carried `written` lists every unwidening pair as related; one cached before the writers has neither. */
export function cardTiers(card) {
  const members = card?.grapheme?.members ?? [{ code_point: card?.code_point, char: card?.char }]
  // The variants a gallery widens to, characters related otherwise, and the derived tier — forms up to two
  // component substitutions may write this character as, folded after the attested variants, each with a
  // mark, no gallery widening to them. A card cached before the derived tier existed has none.
  const variants = { items: [], related: [], sources: {}, ...card?.variants, derived: card?.variants?.derived ?? [] }
  const grapheme = card?.grapheme ?? card
  const writers = new Set((variants.writers ?? []).map(v => v.code_point))
  // A form standing for this grapheme alone (聞: 𦔴) is one of its 字体, with no tag. A card cached
  // before writers carried `shared` tags every writer, as they all were.
  const sharedWriters = new Set((variants.writers ?? []).filter(v => v.shared !== false).map(v => v.code_point))
  const writes = variants.writes ?? []
  // A form with no character of its own (𠮮) has the graphemes it writes as its 字種, and not itself;
  // the 兼用 tag marks each of several.
  const selfGrapheme = variants.own !== false || !writes.length
  const shared = writes.length + (selfGrapheme ? 1 : 0) > 1
  const own = new Set([...members, ...writes].map(member => member.code_point))
  const written = [...variants.items, ...variants.related.filter(v => v.written || writers.has(v.code_point))]
    .filter(v => !own.has(v.code_point)).sort((a, b) => writers.has(b.code_point) - writers.has(a.code_point))
  const related = variants.related.filter(v => !v.written && !writers.has(v.code_point) && !own.has(v.code_point))
  // A shape Unicode lacks has no character page, so it joins the 字体 as its own sequence.
  const described = variants.forms ?? []
  return { members, variants, grapheme, writers, sharedWriters, writes, selfGrapheme, shared, written, related, described }
}

// A CJK compatibility ideograph (說 U+F96F) is an encoding duplicate of a unified one, not a written form.
const compatibility = char => { const p = char.codePointAt(0); return char.length <= 2 && ((p >= 0xf900 && p <= 0xfaff) || (p >= 0x2f800 && p <= 0x2fa1f)) }

/** What a selection on a character's page can be marked as without a search: the 字種 and the 字体 the
 *  card lists, each once, in its order. Related characters and the derived tier write other words or
 *  are inferred, so they stay behind the search box. */
export function cardTargets(card) {
  if (!card?.char) return []
  const { members, grapheme, writes, selfGrapheme, written, described } = cardTiers(card)
  const seen = new Set()
  return [...(selfGrapheme ? [grapheme] : []), ...writes, ...members, ...written, ...described]
    .filter(v => v?.char && !compatibility(v.char) && !seen.has(v.char) && seen.add(v.char))
    .map(v => ({ char: v.char, code_point: v.code_point, script: v.script }))
}
