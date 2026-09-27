/** The reader's colour scheme: the system's, or light or dark whatever the system says. */
export const THEMES = ['system', 'light', 'dark']
export const THEME_COOKIE = 'atlas.theme'
export const isTheme = value => THEMES.includes(value)

/** Show `value` now and keep it for the next visit; the server reads the cookie to render the page in it. */
export function setTheme(value) {
  if (!isTheme(value)) return
  document.documentElement.dataset.theme = value
  document.cookie = `${THEME_COOKIE}=${value}; path=/; max-age=31536000; samesite=lax`
  showThemeColor(value)
}

/** The browser's own bar follows the chosen scheme: each theme-color meta names the scheme it is for. */
export function showThemeColor(value) {
  for (const meta of document.querySelectorAll('meta[name=theme-color][data-scheme]'))
    meta.media = value === 'system' ? `(prefers-color-scheme: ${meta.dataset.scheme})` : meta.dataset.scheme === value ? 'all' : 'not all'
}
