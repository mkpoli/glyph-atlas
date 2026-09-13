export async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  })
  const value = await response.json()
  if (!response.ok) {
    const error = new Error(typeof value.detail === 'string' ? value.detail : response.status === 409
      ? 'This character changed. Reload to review the current version.' : 'The review could not be saved.')
    error.status = response.status
    throw error
  }
  return value
}
export const catalogue = (params = {}) => request('/atlas?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null)))
export const character = id => request('/atlas/characters/' + encodeURIComponent(id))
export const randomSeed = () => Math.floor(Math.random() * 2147483647)
export const number = n => Number(n ?? 0).toLocaleString()
export function stored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback } catch { return fallback }
}
export function remember(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* Server records remain available. */ }
}
export function reviewer() {
  const key = 'atlas.reviewer'
  let id = stored(key, null)
  if (!id) { id = 'reviewer-' + crypto.randomUUID().slice(0, 8); remember(key, id) }
  return id
}
export function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
