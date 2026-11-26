/** A crop's style: the values it may be given, what choosing one does, and saving it. */
import { request } from './client.js'

/** The values of `data/vocab/style.yaml` a single crop may be written in, in the order a reader reaches for them. */
export const CROP_STYLES = ['regular', 'running', 'cursive', 'clerical', 'seal', 'ming', 'gothic']

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

/** The most crops one style request names, as the site takes them. */
export const STYLE_BATCH = 48
// One submission id per batch of crops, revisions and style, so a retry after a lost answer is the same write.
// A batch the site answered drops its id: the same batch sent again later is a new write.
const batches = new Map()
const batchKey = (crops, style) => [style, ...crops.map(crop => `${crop.id}@${crop.revision ?? ''}`)].join('\u0000')
function batchId(key) {
  if (!batches.has(key)) batches.set(key, crypto.randomUUID())
  return batches.get(key)
}

/** The style fields of a crop as a style save answers them, to lay over the record a view holds. */
export const styleOf = ({ revision, style, style_basis, style_inherited }) => ({ revision, style, style_basis, style_inherited })

/**
 * Give `crops` the style, or with 'unassessed' the one each inherits, a batch at a time. A crop with no
 * revision is styled as it stands. Answers `{ styled, refused, kept }`: each crop saved as `{ before, after }`
 * (`after.was` is the style it had of its own before, null for none), those the site refused as `{ id, reason }`,
 * and those kept on this device while writes are paused. A failure carries the same three for the batches
 * sent before it.
 */
export async function styleCrops(crops, style) {
  const styled = [], refused = [], kept = []
  for (let from = 0; from < crops.length; from += STYLE_BATCH) {
    const part = crops.slice(from, from + STYLE_BATCH), key = batchKey(part, style)
    let result
    try {
      result = await request('/atlas/styles', { id: batchId(key), style,
        crops: part.map(crop => crop.revision == null ? { id: crop.id } : { id: crop.id, revision: crop.revision }) })
    } catch (error) {
      if (error?.kept) { kept.push(...part); continue }
      if (error?.status) batches.delete(key)
      throw Object.assign(error ?? new Error(), { styled, refused, kept })
    }
    batches.delete(key)
    const byId = new Map(part.map(crop => [crop.id, crop]))
    for (const now of result.crops ?? []) if (byId.has(now.id)) styled.push({ before: byId.get(now.id), after: { ...byId.get(now.id), ...now } })
    refused.push(...result.refused ?? [])
  }
  return { styled, refused, kept }
}

/** Take back `styleCrops`: each crop goes back to the style it had of its own, or to the one it inherits. A
 *  failure carries what was taken back before it, as `styleCrops` does. */
export async function unstyleCrops(styled) {
  const back = Map.groupBy(styled, ({ before, after }) => (after.was !== undefined ? after.was : ownStyle(before)) ?? 'unassessed')
  const done = { styled: [], refused: [], kept: [] }
  const take = (group, result) => {
    const was = new Map(group.map(({ before, after }) => [after.id, before]))
    done.styled.push(...result.styled.map(({ after }) => ({ before: after, after: { ...was.get(after.id), ...styleOf(after) } })))
    done.refused.push(...result.refused); done.kept.push(...result.kept)
  }
  for (const [style, group] of back) {
    // The crops go back from the revision the style left them at.
    try { take(group, await styleCrops(group.map(({ after }) => after), style)) }
    catch (error) { take(group, { styled: error.styled ?? [], refused: error.refused ?? [], kept: error.kept ?? [] }); throw Object.assign(error, done) }
  }
  return done
}
