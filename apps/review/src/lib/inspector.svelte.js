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
 * The queue and the callbacks cannot be stored in an entry; they stay here until the next `inspect`.
 */
export function createInspector() {
  const state = $state({ onVerdict: null, queue: [] })
  let updateItem = null, lastFocus = null
  function show(id, origin, push) {
    const entry = { ...page.state, inspect: { id, origin } }
    if (push) pushState(cropAddress(id, origin), entry)
    else replaceState(cropAddress(id, origin), entry)
  }
  return {
    state,
    /** The crop on show, as `{ id, origin }`, or null. */
    get shown() { return page.state.inspect ?? null },
    get index() { return state.queue.findIndex(item => item.id === page.state.inspect?.id) },
    get update() { return updateItem },
    inspect(id, decision = null, collection = [], update = null, origin = 'collection') {
      // A crop opened from inside the inspector takes the open one's entry rather than stacking another.
      const open = Boolean(page.state.inspect)
      if (!open) lastFocus = document.activeElement
      Object.assign(state, { onVerdict: decision, queue: [...collection] })
      updateItem = update
      show(id, origin, !open)
    },
    step(direction) {
      const item = state.queue[this.index + direction]
      if (item) show(item.id, item.origin ?? 'collection', false)
    },
    /** Back to the list's entry; the layout returns the focus once the dialog has gone. */
    close() { if (page.state.inspect) history.back() },
    /** A new page has nothing of the last one's to step through. */
    forget() { Object.assign(state, { onVerdict: null, queue: [] }); updateItem = null; lastFocus = null },
    restoreFocus() { if (lastFocus?.isConnected) lastFocus.focus() },
  }
}

export const provideInspector = inspector => setContext(KEY, inspector)
export const useInspector = () => getContext(KEY)
