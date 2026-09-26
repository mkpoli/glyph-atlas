import { catalogue, randomSeed, request } from '$lib/client.js'
import { gallery } from '$lib/layers.js'

// The homepage as the collection view first shows it: one shuffle of the collection's rows, the corpus
// sample drawn with the same seed, and the progress line.
// A collection that cannot answer leaves the view to load and report it itself, with its retry.
export async function load({ fetch }) {
  const seed = randomSeed()
  const [result, sample, collection] = await Promise.all([
    catalogue({ group: 'all', state: 'all', seed, offset: 0, limit: 60 }, { fetch }).catch(() => null),
    gallery(60, seed, { fetch }).catch(() => null),
    request('/atlas/collection/status', undefined, { fetch }).catch(() => null),
  ])
  return { explore: result ? { seed, result, sample, collection } : null }
}
