import { TYPEFACE_VERSION } from '../../../cloudflare/src/typefaceVersions.ts'

/** The face characters are set in: Atlas Sans or Atlas Serif (docs/typefaces.md). */
export const TYPEFACES = ['sans', 'serif']
export const TYPEFACE_COOKIE = 'atlas.typeface'
export const isTypeface = value => TYPEFACES.includes(value)
/** The style sheet that declares a face's files, each with the characters it draws. */
export const typefaceSheet = value => `/atlas/fonts/${TYPEFACE_VERSION}/${value}.css`

/** Show `value` now and keep it for the next visit; the server reads the cookie to render the page in it. */
export function setTypeface(value) {
  if (!isTypeface(value)) return
  document.documentElement.dataset.typeface = value
  document.getElementById('typeface-sheet')?.setAttribute('href', typefaceSheet(value))
  document.cookie = `${TYPEFACE_COOKIE}=${value}; path=/; max-age=31536000; samesite=lax`
}
