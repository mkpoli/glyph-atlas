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

/** The `color-scheme` meta and the media of the theme-color meta for `scheme`, the browser's own bar. */
export const colorScheme = value => value === 'system' ? 'light dark' : value
export const themeColorMedia = (value, scheme) => value === 'system' ? `(prefers-color-scheme: ${scheme})` : value === scheme ? 'all' : 'not all'

/** The metas follow the chosen scheme; the server writes them the same way into the page it renders. */
export function showThemeColor(value) {
  document.querySelector('meta[name=color-scheme]')?.setAttribute('content', colorScheme(value))
  for (const meta of document.querySelectorAll('meta[name=theme-color][data-scheme]')) meta.media = themeColorMedia(value, meta.dataset.scheme)
}
