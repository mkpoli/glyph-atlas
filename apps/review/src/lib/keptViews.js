// What a gallery showed when the reader left it, kept in this tab's memory so Back returns to the same
// crops in the same places, with the page's scroll position restored over them. A link or a reload opens
// the gallery afresh: only a Back or Forward navigation (`popstate`) reads what was kept here.
const views = new Map()
let returning = false

/** Called before every navigation, with SvelteKit's navigation type. */
export function noteNavigation(type) { returning = type === 'popstate' }

/** Keeps what the gallery at `address` shows. */
export function keepView(address, state) {
  views.delete(address)
  views.set(address, state)
  // A visit holds a handful of galleries; the oldest go first.
  while (views.size > 8) views.delete(views.keys().next().value)
}

/** What the gallery at `address` showed, when the reader came back to it. */
export function keptView(address) { return returning ? views.get(address) ?? null : null }
