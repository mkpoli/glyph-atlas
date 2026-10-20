// A batch correction marks many crops as one character, a request per batch, and is undone batch by
// batch. Both are a run of saves made one after another.

// One submission id per batch: the character and each crop at its revision. The same batch sent again
// while its first sending is kept or went unanswered is the same submission, which the site records
// once. A batch the site answered or refused (a 4xx) is done with, and the same crops sent later
// (after an undo, say) are a new submission. After a 5xx the site may have recorded it, so it keeps
// its id.
const submissions = new Map()

/** `crops` cut into batches of `size`, each `{ id, key, crops }` with the submission id its contents name. */
export function correctionBatches(character, crops, size) {
  const batches = []
  for (let from = 0; from < crops.length; from += size) {
    const part = crops.slice(from, from + size)
    const key = [character, ...part.map(crop => crop.id + '@' + crop.revision)].join('\u0000')
    if (!submissions.has(key)) {
      if (submissions.size > 256) submissions.delete(submissions.keys().next().value)
      submissions.set(key, crypto.randomUUID())
    }
    batches.push({ id: submissions.get(key), key, crops: part })
  }
  return batches
}

const forget = batch => { if (submissions.get(batch.key) === batch.id) submissions.delete(batch.key) }

/**
 * Send `batches` from `correctionBatches` with `sendEach`. Each batch answered, and one the site
 * refused with a 4xx, gives up its id; a kept one, one answered with a 5xx, or one whose sending failed
 * before the site answered, keeps it.
 */
export async function sendBatches(batches, send) {
  try {
    const done = await sendEach(batches, send)
    done.answered.forEach(({ item }) => forget(item))
    return done
  } catch (error) {
    error.answered.forEach(({ item }) => forget(item))
    if (error.status >= 400 && error.status < 500) forget(error.item)
    throw error
  }
}

/**
 * Send `items` in order, one at a time, with `send`; answers `{ answered: [{ item, result }], kept }`.
 * One kept on this device while writes are paused does not stop the run: the rest are kept after it,
 * in order, and all go once writes resume. Any other refusal stops it, and the error carries what came
 * before it as `answered` and `kept`, and the item refused as `item`.
 */
export async function sendEach(items, send) {
  const answered = [], kept = []
  for (const item of items) {
    try { answered.push({ item, result: await send(item) }) }
    catch (error) {
      if (error?.kept) { kept.push(item); continue }
      throw Object.assign(error ?? new Error(), { answered, kept, item })
    }
  }
  return { answered, kept }
}
