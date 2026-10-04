import { getContext, setContext } from 'svelte'
import { pushState, replaceState } from '$app/navigation'
import { page } from '$app/state'
import { localize } from './i18n.svelte.js'

const KEY = Symbol('inspector')

/** A crop's own page, which draws it over the collection when it is loaded directly. */
export const cropAddress = (id, origin = 'collection') =>
  localize((origin === 'corpus' ? '/corpus/' : '/crop/') + encodeURIComponent(id))

/**
 * The crop inspector every view opens: which crop is shown, the collection it steps through, and who
 * hears about a verdict or a save. The layout owns it and draws the dialog; views only call `inspect`.
 *
 * The crop on show is kept in the history entry (`page.state.inspect`) and the address bar shows its
 * page. Opening a crop adds an entry over the list, stepping to another replaces it, and closing goes
 * back to the list's entry, so Back and Forward close and reopen it and the list keeps its scroll.
 * The queue and the callbacks cannot be stored in an entry; they stay here under the key of the
 * `inspect` that set them, and an entry with another key (Forward to a crop opened before) has none.
 */
export function createInspector() {
  const state = $state({ onVerdict: null, queue: [], key: 0 })
  let updateItem = null, lastFocus = null, closing = false
  // What this page saved for each crop, so stepping back to one shows the decision it was given.
  const decisions = {}
  const current = () => page.state.inspect?.key === state.key
  function show(id, origin, push) {
    closing = false
    const entry = { ...page.state, inspect: { id, origin, key: state.key } }
    if (push) pushState(cropAddress(id, origin), entry)
    else replaceState(cropAddress(id, origin), entry)
  }
  return {
    state,
    /** The crop on show, as `{ id, origin }`, or null. */
    get shown() { return page.state.inspect ?? null },
    get queue() { return current() ? state.queue : [] },
    get index() { return current() ? state.queue.findIndex(item => item.id === page.state.inspect?.id) : -1 },
    get onVerdict() { return current() ? state.onVerdict : null },
    get update() { return current() ? updateItem : null },
    inspect(id, decision = null, collection = [], update = null, origin = 'collection') {
      // A crop opened from inside the inspector takes the open one's entry rather than stacking another.
      const open = Boolean(page.state.inspect)
      if (!open) lastFocus = document.activeElement
      Object.assign(state, { onVerdict: decision, queue: [...collection], key: state.key + 1 })
      updateItem = update
      show(id, origin, !open)
    },
    step(direction) {
      const item = state.queue[this.index + direction]
      if (item) show(item.id, item.origin ?? 'collection', false)
    },
    /** Back to the list's entry; the layout returns the focus once the dialog has gone. */
    close() {
      // A second close before the first Back lands would leave the list too.
      if (!page.state.inspect || closing) return
      closing = true
      history.back()
    },
    /** The crop on show was replaced by another; the entry names the one on screen. */
    replaced(id) {
      if (page.state.inspect) replaceState(cropAddress(id, page.state.inspect.origin), { ...page.state, inspect: { ...page.state.inspect, id } })
      else replaceState(cropAddress(id), page.state)
    },
    /** A new page has nothing of the last one's to step through. */
    forget() { Object.assign(state, { onVerdict: null, queue: [], key: state.key + 1 }); updateItem = null; lastFocus = null },
    /** The entry changed (a close landed, or Forward reopened a crop): a close may go back again. */
    settle() { closing = false },
    restoreFocus() { if (lastFocus?.isConnected) lastFocus.focus() },
    remember(id, decision) { decisions[id] = decision },
    decided(id) { return decisions[id] ?? null },
  }
}

export const provideInspector = inspector => setContext(KEY, inspector)
export const useInspector = () => getContext(KEY)
