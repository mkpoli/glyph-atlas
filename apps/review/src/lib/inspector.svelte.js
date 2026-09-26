import { getContext, setContext } from 'svelte'

const KEY = Symbol('inspector')

/**
 * The crop inspector every view opens: which crop is shown, the collection it steps through, and who
 * hears about a verdict or a save. The layout owns it and draws the dialog; views only call `inspect`.
 */
export function createInspector() {
  const state = $state({ selected: null, origin: 'collection', onVerdict: null, queue: [] })
  let updateItem = null, lastFocus = null
  return {
    state,
    get index() { return state.queue.findIndex(item => item.id === state.selected) },
    get update() { return updateItem },
    inspect(id, decision = null, collection = [], update = null, origin = 'collection') {
      lastFocus = document.activeElement
      Object.assign(state, { selected: id, origin, onVerdict: decision, queue: [...collection] })
      updateItem = update
    },
    step(direction) {
      const item = state.queue[this.index + direction]
      if (item) Object.assign(state, { selected: item.id, origin: item.origin ?? 'collection' })
    },
    close() {
      Object.assign(state, { selected: null, onVerdict: null, queue: [] })
      updateItem = null
      lastFocus?.focus()
    },
  }
}

export const provideInspector = inspector => setContext(KEY, inspector)
export const useInspector = () => getContext(KEY)
