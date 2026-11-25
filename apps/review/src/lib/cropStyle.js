/** A crop's style: the values it may be given, what choosing one does, and saving it. */
import { request } from './client.js'

/**
 * The values of `data/vocab/style.yaml` a reader may give a single crop, in the order they are reached for. A
 * page or document can be in mixed styles, and a print face (`ming`, `gothic`) is not told apart from regular
 * script on one crop.
 */
export const CROP_STYLES = ['regular', 'running', 'cursive', 'clerical', 'seal']

/** The style a reader gave the crop itself, or null while it takes its page's or document's. */
export const ownStyle = item => item?.style_basis === 'unit' ? item.style : null
/** The style a crop without its own takes, or null where nobody assessed one or the server names none. */
export const inheritedStyle = item => CROP_STYLES.includes(item?.style_inherited) ? item.style_inherited : null

/**
 * The styles offered for a crop: those the server can store (`style_choices`), and the one it has now,
 * which a reader may take back or confirm.
 */
export function stylesOffered(item) {
  const own = ownStyle(item), listed = item?.style_choices ?? CROP_STYLES
  const offered = CROP_STYLES.filter(style => listed.includes(style) || style === own)
  return own && !offered.includes(own) ? [...offered, own] : offered
}

/**
 * The choice pressing `style` leaves: a style to give the crop, 'unassessed' to take its own back, or null
 * for no change. `chosen` is the choice so far.
 */
export function pressStyle(item, chosen, style) {
  const own = ownStyle(item)
  if (style === (chosen ?? own)) return chosen == null ? 'unassessed' : null
  return style === own ? null : style
}

// One submission id per crop, revision and style: the same choice made again (after a failure, or while
// an earlier one is kept on this device) is the same submission, which the site records once.
const submissions = new Map()
function submission(item, style) {
  const key = [item.id, item.revision, style].join('\u0000')
  if (!submissions.has(key)) submissions.set(key, crypto.randomUUID())
  return submissions.get(key)
}

/** Give `item` the style, or with 'unassessed' the one it inherits. Answers the crop as it now stands. */
export const setStyle = (item, style) => request(`/atlas/characters/${encodeURIComponent(item.id)}/style`,
  { id: submission(item, style), revision: item.revision, style })
