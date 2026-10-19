import { request } from './client.js'

/** Words spelled by this crop's characters, followed by matches for a reading. */
export async function wordChoices(crop, q = '') {
  const chars = [...new Set([...(crop.label ?? ''), ...(crop.written_character ?? '')])].slice(0, 4).join('')
  return (await request('/layers/word-choices?' + new URLSearchParams({ q, c: chars }))).items
}

// A lost answer keeps its submission id; an answered save of the same value is a new claim.
const submissions = new Map()
export async function setWord(crop, { word, method, form }) {
  const value = word === null ? { word: null } : { word, method, form }
  const key = JSON.stringify([crop.id, crop.crop_version, value])
  if (!submissions.has(key)) submissions.set(key, crypto.randomUUID())
  let result
  try {
    result = await request(`/atlas/characters/${encodeURIComponent(crop.id)}/word`,
      { id: submissions.get(key), crop_version: crop.crop_version, ...value })
  } catch (error) { if (error.status) submissions.delete(key); throw error }
  submissions.delete(key)
  return { crop: { ...crop, word: result.word }, replaced: result.replaced ?? null }
}

export const clearWord = crop => setWord(crop, { word: null })
