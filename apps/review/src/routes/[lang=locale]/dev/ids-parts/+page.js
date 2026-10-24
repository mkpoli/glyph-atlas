import { dev } from '$app/environment'
import { error } from '@sveltejs/kit'

// A page for trying the IDS parts palette on its own; it exists only on the development server.
export function load() {
  if (!dev) error(404)
}
