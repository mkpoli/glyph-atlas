import { request } from './client.js'

/** Words spelled by this crop's characters, followed by matches for a reading. */
export async function wordChoices(crop, q = '') {
  const chars = [...new Set([...(crop.label ?? ''), ...(crop.written_character ?? '')])].slice(0, 4).join('')
  return (await request('/layers/word-choices?' + new URLSearchParams({ q, c: chars }))).items
}
