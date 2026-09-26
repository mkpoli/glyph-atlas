import { decide } from './forms.js'
import { reviewer } from './client.js'

// The form decisions made in this tab, one step per action. A step keeps every decision it sent and,
// for each, the decisions the server named to restore what it changed. Both kinds set a state rather
// than change one, so sending either again after a failure does no harm.
export const history = $state({ done: [], undone: [], busy: false })

/** Runs `action`, which sends its decisions through the `send` it is given, as one step. `place`
 *  (`{ family, cluster }`) is where the step happened, to return to when it is taken back. */
export async function step(place, action) {
  const sent = []
  const send = async decision => {
    const result = await decide(decision)
    sent.push({ decision, undo: result.undo })
    return result
  }
  try { return await action(send) } finally {
    if (sent.length) { history.done.push({ ...place, sent }); history.undone = [] }
  }
}

/** Takes back the last step; returns it, or null when there is none. */
export async function undo() {
  const last = history.done.at(-1)
  if (!last || history.busy) return null
  history.busy = true
  try {
    for (const { undo } of [...last.sent].reverse())
      for (const decision of undo) await decide({ ...decision, client_id: reviewer() })
    history.done.pop(); history.undone.push(last)
    return last
  } finally { history.busy = false }
}

/** Makes the last step taken back again; returns it, or null when there is none. */
export async function redo() {
  const next = history.undone.at(-1)
  if (!next || history.busy) return null
  history.busy = true
  const sent = []
  try {
    for (const { decision } of next.sent) sent.push({ decision, undo: (await decide(decision)).undo })
    return next
  } finally {
    // Should sending stop partway, what was sent becomes the step to take back, and the rest is dropped.
    if (sent.length) { history.undone.pop(); history.done.push({ ...next, sent }) }
    history.busy = false
  }
}
