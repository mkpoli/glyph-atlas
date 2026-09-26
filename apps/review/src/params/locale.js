import { LOCALES } from '$lib/i18n.svelte.js'

// A language prefix: every interface language but English, which has the unprefixed addresses.
export const match = value => value !== 'en' && LOCALES.some(locale => locale.tag === value)
