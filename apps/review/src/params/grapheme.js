import { unslug } from '$lib/gallery.js'

// A grapheme as an address writes it: `U+85CF`, or a sequence `U+304B-U+309A`.
export const match = value => unslug(value) !== null
