import { error } from '@sveltejs/kit'
import { formGlyphs } from '$lib/forms.js'

// A described form's own page: the crops its `has_form` claims name, read from the forms tool.
export async function load({ fetch, params }) {
  try {
    return { form: await formGlyphs(params.id, { fetch }) }
  } catch (e) {
    error(e.status === 404 ? 404 : 503, { message: e.message, code: e.code === 'busy' ? 'busy' : undefined })
  }
}
