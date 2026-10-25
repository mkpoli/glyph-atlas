// What the editor's preview says under each step: its label, and which credits go with it.

/**
 * The caption of a preview: `{ label, params, credits }`, `label` a key under `form.ids.preview.`,
 * `credits` the key of the sources line, or null. `step` is `rough`, `capped` (too large to draw),
 * `failed`, `composed` or `final`; `drawn` is the composer's answer (`compose` in `index.js`).
 */
export function caption(step, drawn = null) {
  if (step === 'final') return { label: 'final', params: {}, credits: 'credits' }
  if (step === 'failed') return { label: 'failed', params: {}, credits: null }
  if (step === 'capped') return { label: 'tooLarge', params: {}, credits: null }
  if (step !== 'composed' || !drawn) return { label: 'draft', params: {}, credits: null }
  const credits = drawn.kanjivg ? 'credits' : 'creditsFonts'
  if (drawn.approximate?.length) return { label: 'approximate', params: { ops: drawn.approximate.join(' ') }, credits }
  return { label: 'draft', params: {}, credits }
}
