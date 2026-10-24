// What a gallery showed when the reader left it, kept in this tab's memory so Back returns to the same
// crops in the same places, with the page's scroll position restored over them. A link or a reload opens
// the gallery afresh: only a Back or Forward navigation (`popstate`) reads what was kept here.
//
// Views are kept by history entry (SvelteKit's index in `history.state`), so two visits to the same
// address keep a view each. Any write the site is sent drops them all: what they show may have changed.
const HISTORY_INDEX = 'sveltekit:history'
const views = new Map()
let returning = false

/** The history entry the browser is on. */
export const entry = () => typeof history === 'undefined' ? null : history.state?.[HISTORY_INDEX] ?? null

/** Called before every navigation, with SvelteKit's navigation type. */
export function noteNavigation(type) { returning = type === 'popstate' }

/** Called on every write to the site. */
export function noteWrite() { views.clear() }

/** Keeps what the gallery shows under each of `entries`. */
export function keepView(entries, state) {
  for (const key of new Set(entries)) {
    if (key == null) continue
    views.delete(key)
    views.set(key, state)
  }
  // A visit holds a handful of galleries; the oldest go first.
  while (views.size > 8) views.delete(views.keys().next().value)
}

/** What the gallery on the current history entry showed, when the reader came back to it. */
export function keptView() { return returning ? views.get(entry()) ?? null : null }
