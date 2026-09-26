import { getContext, setContext } from 'svelte'
import { reviewer, stored, remember } from './client.js'

const KEY = Symbol('session')

/**
 * What the layout knows about this browser and shares with every view: the reviewer id, which exists
 * only once the page runs in a browser, the image style, and the collection-progress dialog.
 */
export function createSession() {
  const state = $state({ clientId: '', ink: 'original', progress: false })
  return {
    state,
    start() {
      state.clientId = reviewer()
      // A manuscript scan is a colour photograph of paper and ink, so Original is the default and B&W
      // is the reader's choice. One preference serves the collection, the round and the reviewer.
      state.ink = stored('atlas.ink', 'original') === 'bw' ? 'bw' : 'original'
    },
    setInk(value) { state.ink = value; remember('atlas.ink', value) },
    showProgress() { state.progress = true },
  }
}

export const provideSession = session => setContext(KEY, session)
export const useSession = () => getContext(KEY)
