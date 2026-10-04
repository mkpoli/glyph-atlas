// How a run's letterforms were made, as the site's `hand_order` groups its first crop (migration 0062):
// shaped by hand, not known, and set from type. A run's page lists them in this order and narrows to one.
export const HAND_GROUPS = ['hand', 'unknown', 'type']
/** The productions (`data/vocab/production.yaml`) in each group; a running or cursive style also places a
 *  crop of any production but type in `hand`. */
export const PRODUCTIONS_IN = {
  hand: ['handwritten', 'inscribed', 'printed', 'printed/woodblock', 'printed/engraved', 'printed/lithograph', 'printed/stencil'],
  unknown: ['unknown', 'mixed'],
  type: ['printed/type', 'printed/phototype', 'printed/digital', 'typewritten'],
}
/** A group an address may name, or '' for all of them. */
export const handParam = value => HAND_GROUPS.includes(value) ? value : ''
