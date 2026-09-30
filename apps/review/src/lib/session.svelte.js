import { getContext, setContext } from 'svelte'
import { reviewer, stored, remember } from './client.js'

const KEY = Symbol('session')

/**
 * What the layout knows about this browser and shares with every view: the reviewer id, which exists
 * only once the page runs in a browser, the image style, whether a save in the inspector goes on to
 * the next crop, and the collection-progress dialog.
 */
export function createSession() {
  const state = $state({ clientId: '', ink: 'original', advance: false, progress: false })
  return {
    state,
    start() {
      state.clientId = reviewer()
      // A manuscript scan is a colour photograph of paper and ink, so Original is the default and B&W
      // is the reader's choice. One preference serves the collection, the round and the reviewer.
      state.ink = stored('atlas.ink', 'original') === 'bw' ? 'bw' : 'original'
      // Most crops in the collection are right, so a save closes the inspector unless the reader has
      // chosen to go through them in a row.
      state.advance = stored('atlas.advance', false) === true
    },
    setInk(value) { state.ink = value; remember('atlas.ink', value) },
    setAdvance(value) { state.advance = value; remember('atlas.advance', value) },
    showProgress() { state.progress = true },
  }
}

export const provideSession = session => setContext(KEY, session)
export const useSession = () => getContext(KEY)
