// The site's graphemes as a search box's cards show them, for a page that does not list them itself: the
// crops of each by written form, whatever the page is narrowed to, read when the box is first used.
import { graphemeCounts } from './client.js'
import { codesOf, cropTotals, gatherGraphemes } from './graphemeGroups.js'

/** The group among `groups` (by key) that holds a candidate's own character, or null. */
export function groupOf(groups, item) {
  const own = codesOf(item.char ?? ''), group = groups.get(item.grapheme?.code_point ?? own) ?? groups.get(own)
  return group && group.members.some(member => member.label === item.char) ? group : null
}

export class SearchGroups {
  #counts = $state.raw(null)
  #asked = false
  #groups = $derived(new Map(gatherGraphemes({ counts: this.#counts ?? [] }).map(group => [group.key, group])))
  #forms = $derived(this.#counts && cropTotals(this.#counts).forms)
  async read() {
    if (this.#asked) return
    this.#asked = true
    // Without them the cards show the candidates uncounted; the next use asks again.
    try { this.#counts = await graphemeCounts() } catch { this.#asked = false }
  }
  groupOf = item => groupOf(this.#groups, item)
  /** A character's crops, or undefined where they are not known. */
  countOf = char => this.#forms ? this.#forms.get(char) ?? 0 : undefined
}
