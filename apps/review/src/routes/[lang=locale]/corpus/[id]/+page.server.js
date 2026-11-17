import { corpusCharacter } from '$lib/client.js'
import { loadCropPage } from '$lib/cropPage.js'

// A citation's address names the cut it cites (`?v=`); the page notes it when the glyph was cut again since.
export const load = event => loadCropPage(corpusCharacter, 'corpus', event)
