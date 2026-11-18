import { character } from '$lib/client.js'
import { loadCropPage } from '$lib/cropPage.js'

// A citation's address names the cut it cites (`?v=`); the page notes it when the crop was cut again since.
export const load = event => loadCropPage(character, event)
