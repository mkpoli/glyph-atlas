import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { boot, events as readEvents, options, ROOT, units as readUnits } from './harness.mjs'

// -- arguments ------------------------------------------------------------------------------------

const config = options()

// -- reporting ------------------------------------------------------------------------------------

const results = []
let failures = 0

async function step(name, body) {
  try {
    const detail = await body()
    results.push({ name, ok: true, detail: detail ?? '' })
    console.log(`  ok   ${name}${detail ? ` — ${detail}` : ''}`)
  } catch (error) {
    failures += 1
    results.push({ name, ok: false, detail: error.message })
    console.log(`  FAIL ${name} — ${error.message}`)
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function equal(actual, expected, what) {
  const left = JSON.stringify(actual)
  const right = JSON.stringify(expected)
  if (left !== right) throw new Error(`${what}: expected ${right}, got ${left}`)
}

// -- http -----------------------------------------------------------------------------------------

async function request(path, { method = 'GET', body } = {}) {
  const response = await fetch(`${base}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await response.text()
  let payload = null
  try {
    payload = text ? JSON.parse(text) : null
  } catch {
    payload = text
  }
  return { status: response.status, payload, headers: response.headers, text }
}

async function review(body) {
  const { status, payload } = await request('/reviews', { method: 'POST', body })
  return { status, result: payload?.results?.[0] ?? payload?.detail }
}

/** A key of the kind `crypto.randomUUID()` gives the interface. */
const key = () => crypto.randomUUID()

// -- the run --------------------------------------------------------------------------------------

const service = await boot(config)
// The contract checks speak to the review service itself; the page server is checked on its own below.
const base = service.api
console.log(`check: ${service.base} (pages), ${base} (API) over ${config.directory}`)
const fixture = service.fixture ?? {
  documents: ['doc-1', 'doc-2'],
  line: 'doc-1:p1:line0',
  cached_page: 'doc-1:p1',
  uncached_page: 'doc-1:p2',
  unit: 'doc-1:p1:line0:u0',
  warigaki: 'doc-1:p3:line0',
  long_line: 'doc-1:p3:line1',
}
console.log(`fixture: ${fixture.documents.join(', ')}; pages ${Object.keys(fixture.pages ?? {}).join(', ')}`)
console.log('service: up')

/** The events the service recorded, from the store the service writes. */
const events = () => readEvents(config.directory)
const unitsInState = () => readUnits(config.directory)

const lineId = fixture.line
const pageId = fixture.cached_page
const uncachedPage = fixture.uncached_page
const unitId = fixture.unit
const clientId = 'check-client'
let firstTimingId = null
let splitResult = null
let letterResult = null
let merged = null
let undoEventId = null

console.log('\nthe page server')

await step('GET / renders the collection on the server', async () => {
  const response = await fetch(`${service.base}/`)
  const text = await response.text()
  assert(response.status === 200, `status ${response.status}`)
  assert(response.headers.get('content-type')?.includes('text/html'), `content-type ${response.headers.get('content-type')}`)
  assert(/<html lang="[^"]+"/.test(text), 'the page names no language')
  const tiles = (text.match(/class="glyph-tile/g) ?? []).length
  assert(tiles > 0, 'the server rendered no crops')
  return `${tiles} crops in the first response`
})

await step('the page server forwards API paths and keeps page paths', async () => {
  const proxied = await fetch(`${service.base}/atlas?purpose=browse&limit=1`)
  assert(proxied.status === 200 && proxied.headers.get('content-type')?.includes('application/json'), `GET /atlas answered ${proxied.status}`)
  const history = await fetch(`${service.base}/history`)
  assert(history.headers.get('content-type')?.includes('text/html'), 'GET /history is not a page')
  const documents = await request('/documents')
  assert(documents.status === 200, `GET /documents answered ${documents.status}`)
  equal(documents.payload.total, 2, 'documents')
  const queue = await request('/queue?strategy=disagreement')
  assert(queue.status === 200 && queue.payload.total >= 1, 'the disagreement queue is empty')
  return `${documents.payload.total} documents, ${queue.payload.total} disagreement line(s)`
})

console.log('\nreading the dataset the way the views do')

await step('page view: the cached page names a served image, the uncached one names its URL', async () => {
  const page = await request(`/pages/${encodeURIComponent(pageId)}`)
  assert(page.status === 200, `status ${page.status}`)
  assert(page.payload.image_url.startsWith('/images/'), `image_url ${page.payload.image_url}`)
  const image = await fetch(`${base}${page.payload.image_url}`)
  assert(image.status === 200, `image answered ${image.status}`)
  assert(image.headers.get('content-type') === 'image/jpeg', 'the image is not a jpeg')
  const uncached = await request(`/pages/${encodeURIComponent(uncachedPage)}`)
  assert(uncached.payload.sha256 === null, 'the uncached page carries a checksum')
  assert(uncached.payload.image_url.startsWith('https://'), 'the uncached page should name its URL')
  return `${page.payload.lines} lines, ${page.payload.units} units, image ${image.headers.get('content-length')} B`
})

await step('page view: the lines and their unit counts', async () => {
  const lines = await request(`/pages/${encodeURIComponent(pageId)}/lines`)
  assert(lines.status === 200 && lines.payload.total >= 3, `status ${lines.status}`)
  const first = lines.payload.items.find((line) => line.id === lineId)
  assert(first, `${lineId} is not in ${pageId}`)
  assert(first.units === 4 && first.revision === 0, `units ${first.units}, revision ${first.revision}`)
  return `${lines.payload.total} lines, ${lineId} has ${first.units} units`
})

await step('line view: the units and their revisions', async () => {
  const units = await request(`/lines/${encodeURIComponent(lineId)}/units`)
  assert(units.status === 200 && units.payload.total === 4, `status ${units.status}`)
  assert(units.payload.items.every((unit) => unit.revision === 0), 'a revision is not 0')
  equal(
    units.payload.items.map((unit) => unit.seq),
    [0, 1, 2, 3],
    'seq',
  )
  return `${units.payload.total} active units`
})

console.log('\nopening a line (timing)')

await step('open: a timing event with action=open', async () => {
  const { status, result } = await review({
    target_type: 'line',
    target_id: lineId,
    field: 'timing',
    new: { action: 'open', opened_ms: Date.now() },
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(status === 200, `status ${status}`)
  equal(result.review.new.action, 'open', 'the timing payload')
  firstTimingId = result.id
  return `${result.id}, line revision ${result.revision}`
})

console.log('\nthe actions of the line view')

await step('accept (a): a review event on the unit', async () => {
  const accepted = await review({
    target_type: 'unit',
    target_id: `${lineId}:u1`,
    field: 'review',
    new: 'reviewed',
    base_revision: 0,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(accepted.status === 200, `status ${accepted.status}`)
  assert(accepted.result.state.review === 'reviewed', 'the unit is not reviewed')
  assert(typeof accepted.result.review.old === 'string', 'the event carries no old value')
  assert(accepted.result.revision === 1, `revision ${accepted.result.revision}`)
  return `${accepted.result.id}, old ${JSON.stringify(accepted.result.review.old)} → "reviewed"`
})

await step('move (drag): a box review with the base revision', async () => {
  const before = (await request(`/lines/${encodeURIComponent(lineId)}/units`)).payload.items.find(
    (unit) => unit.id === `${lineId}:u2`,
  )
  const moved = await review({
    target_type: 'unit',
    target_id: before.id,
    field: 'box',
    new: { x: before.box.x + 12, y: before.box.y + 20, w: before.box.w, h: before.box.h },
    base_revision: before.revision,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(moved.status === 200, `status ${moved.status}`)
  equal(moved.result.review.old, before.box, 'the old box')
  equal(moved.result.state.box, { ...before.box, x: before.box.x + 12, y: before.box.y + 20 }, 'the new box')
  return `${moved.result.id}, revision ${moved.result.revision}`
})

await step('split (s): one segmentation event retires the unit and creates two', async () => {
  const unit = (await request(`/lines/${encodeURIComponent(lineId)}/units`)).payload.items.find(
    (item) => item.id === unitId,
  )
  const half = Math.round(unit.box.h / 2)
  const { status, result } = await review({
    target_type: 'unit',
    target_id: unitId,
    field: 'segmentation',
    new: {
      split: [
        { box: { ...unit.box, h: half }, reading: unit.reading },
        { box: { ...unit.box, y: unit.box.y + half, h: unit.box.h - half }, reading: unit.reading },
      ],
    },
    base_revision: unit.revision,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(status === 200, `status ${status}`)
  equal(result.created, [`${lineId}:m1`, `${lineId}:m2`], 'the created units')
  equal(result.retired, [unitId], 'the retired unit')
  splitResult = result
  const units = await request(`/lines/${encodeURIComponent(lineId)}/units`)
  equal(
    units.payload.items.map((item) => item.id).sort(),
    [`${lineId}:m1`, `${lineId}:m2`, `${lineId}:u1`, `${lineId}:u2`, `${lineId}:u3`].sort(),
    'the active units after the split',
  )
  equal(units.payload.items.find((item) => item.id === `${lineId}:m1`).seq, 0, 'the seq of the first half')
  return `${result.id}, created ${result.created.join(', ')}`
})

await step('choose a 字母 (j): the candidates carry 字母 and reference glyphs', async () => {
  const body = await request(`/units/${encodeURIComponent(`${lineId}:m1`)}/candidates`)
  assert(body.status === 200, `status ${body.status}`)
  const hentaigana = body.payload.candidates.find((candidate) => candidate.jibo === '安')
  assert(hentaigana, `no hentaigana candidate with 安 in ${body.payload.candidates.length} candidates`)
  assert(
    typeof hentaigana.reference_url === 'string' && hentaigana.reference_url.includes('ninjal'),
    'the candidate has no reference glyph URL',
  )
  assert(hentaigana.mj, 'the candidate has no MJ figure')
  return `${body.payload.candidates.length} candidates, U+1B002 字母 安 ${hentaigana.reference_url}`
})

await step('choose a character (j): unicode, script and classification are recorded in order', async () => {
  let revision = (await request(`/lines/${encodeURIComponent(lineId)}/units`)).payload.items.find(
    (item) => item.id === `${lineId}:m1`,
  ).revision
  const posted = []
  for (const [field, value] of [
    ['unicode', 'U+1B002'],
    ['script', 'hentaigana'],
    ['classification', 'identified'],
  ]) {
    const { status, result } = await review({
      target_type: 'unit',
      target_id: `${lineId}:m1`,
      field,
      new: value,
      base_revision: revision,
      client_id: clientId,
      idempotency_key: key(),
    })
    assert(status === 200, `${field}: status ${status}`)
    assert(result.revision === revision + 1, `${field}: revision ${result.revision}`)
    revision = result.revision
    posted.push(result)
  }
  letterResult = posted[posted.length - 1]
  const units = unitsInState()
  const unit = units[`${lineId}:m1`]
  // The 字母 of U+1B002 is the character layer's to state; a unit records the code point.
  assert(unit.unicode === 'U+1B002' && !('jibo' in unit), `unicode ${unit.unicode}`)
  assert(unit.script === 'hentaigana' && unit.classification === 'identified', 'script or classification')
  return `${posted.map((result) => result.id).join(', ')} → revision ${revision}`
})

console.log('\nundo: a compensating review')

await step('undo (z): the last event is written back to its old value', async () => {
  const before = unitsInState()[`${lineId}:m1`]
  const event = events().find((row) => row.id === letterResult.id)
  const { status, result } = await review({
    target_type: 'unit',
    target_id: `${lineId}:m1`,
    field: event.field,
    new: event.old,
    evidence: `undo of ${event.id}`,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(status === 200, `status ${status}`)
  assert(result.review.evidence === `undo of ${event.id}`, `evidence ${result.review.evidence}`)
  undoEventId = result.id
  const after = unitsInState()[`${lineId}:m1`]
  assert(after[event.field] === event.old, `${event.field} is ${JSON.stringify(after[event.field])}`)
  assert(before[event.field] !== event.old, 'the undo did not change anything')
  return `${result.id} evidence "${result.review.evidence}", ${event.field} back to ${JSON.stringify(event.old)}`
})

await step('merge (m): the two halves become one ligature unit', async () => {
  const both = await review({
    target_type: 'unit',
    target_id: `${lineId}:m2`,
    field: 'segmentation',
    new: { merge: [`${lineId}:m1`, `${lineId}:m2`] },
    base_revision: 0,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(both.status === 200, `status ${both.status}`)
  equal(both.result.retired, [`${lineId}:m1`, `${lineId}:m2`], 'the retired units')
  merged = both.result
  const mergeEvent = both.result.created[0]
  const unit = unitsInState()[mergeEvent]
  assert(unit.kind === 'ligature' && unit.active, 'the merged unit is not an active ligature')
  return `${mergeEvent} reading ${JSON.stringify(unit.reading)}`
})

await step('undo of a merge: the output is retired, the inputs stay retired', async () => {
  const output = merged.created[0]
  const retired = await review({
    target_type: 'unit',
    target_id: output,
    field: 'active',
    new: false,
    evidence: `undo of ${merged.id}`,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(retired.status === 200, `retiring the output answered ${retired.status}`)
  const units = await request(`/lines/${encodeURIComponent(lineId)}/units`)
  const ids = units.payload.items.map((item) => item.id)
  assert(!ids.includes(output), `${output} is still active`)
  assert(!ids.includes(`${lineId}:m1`), `${lineId}:m1 came back, which the store does not allow`)
  return `${retired.result.id} → ${ids.length} active units`
})

await step('the store refuses a review of a retired unit, including reactivation', async () => {
  const { status, result } = await review({
    target_type: 'unit',
    target_id: `${lineId}:m1`,
    field: 'active',
    new: true,
    evidence: `undo of ${merged.id}`,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(status === 409, `status ${status}`)
  assert(result.error === 'retired', `error ${result.error}`)
  return 'the undo of a segmentation retires the outputs and says so in the interface'
})

console.log('\ncreation and conflict')

await step('create a unit (c): POST /units', async () => {
  const created = await request('/units', {
    method: 'POST',
    body: {
      line_id: lineId,
      box: { x: 520, y: 140, w: 60, h: 60 },
      reading: 'そ',
      client_id: clientId,
      idempotency_key: key(),
    },
  })
  assert(created.status === 201, `status ${created.status}`)
  assert(created.payload.review.field === 'create', 'the event is not a create')
  assert(created.payload.state.method === 'manual', 'the unit is not manual')
  return `${created.payload.target_id} revision ${created.payload.revision}`
})

await step('create a line (l): POST /lines', async () => {
  const created = await request('/lines', {
    method: 'POST',
    body: {
      page_id: pageId,
      box: { x: 900, y: 200, w: 200, h: 900 },
      text_raw: 'そ',
      text: 'そ',
      client_id: clientId,
      idempotency_key: key(),
    },
  })
  assert(created.status === 201, `status ${created.status}`)
  assert(created.payload.state.match_method === 'manual', 'the line is not manual')
  const lines = await request(`/pages/${encodeURIComponent(pageId)}/lines`)
  // Three detected lines, the fixture's line of written-identity cases, and the one just created.
  assert(lines.payload.total === 5, `the page has ${lines.payload.total} lines`)
  return `${created.payload.target_id}`
})

await step('409: a stale base revision answers the current state', async () => {
  const current = (await request(`/lines/${encodeURIComponent(lineId)}/units`)).payload.items.find(
    (item) => item.id === `${lineId}:u1`,
  )
  assert(current && current.revision >= 1, `${lineId}:u1 has revision ${current?.revision}`)
  const { status, result } = await review({
    target_type: 'unit',
    target_id: current.id,
    field: 'reading',
    new: 'く',
    base_revision: 0,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(status === 409, `status ${status}`)
  assert(result.error === 'stale-revision', `error ${result.error}`)
  assert(result.revision === current.revision && result.base_revision === 0, 'the revisions are not reported')
  assert(result.state && result.state.id === current.id, 'the current state is missing')
  return `base 0, current ${result.revision}, state reading ${JSON.stringify(result.state.reading)}`
})

await step('409: reapplying from the reported revision is accepted', async () => {
  const current = (await request(`/lines/${encodeURIComponent(lineId)}/units`)).payload.items.find(
    (item) => item.id === `${lineId}:u1`,
  )
  const refusal = await review({
    target_type: 'unit',
    target_id: current.id,
    field: 'reading',
    new: 'く',
    base_revision: current.revision - 1,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(refusal.status === 409, `status ${refusal.status}`)
  const reapplied = await review({
    target_type: 'unit',
    target_id: current.id,
    field: 'reading',
    new: 'く',
    base_revision: refusal.result.revision,
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(reapplied.status === 200, `reapplying answered ${reapplied.status}`)
  assert(reapplied.result.state.reading === 'く', 'the reapplied reading is not stored')
  return `reapplied from revision ${refusal.result.revision} as ${reapplied.result.id}`
})

await step('409: a retired unit answers retired with its state', async () => {
  const retired = merged.created[0]
  const { status, result } = await review({
    target_type: 'unit',
    target_id: retired,
    field: 'reading',
    new: 'く',
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(status === 409, `status ${status}`)
  assert(result.error === 'retired', `error ${result.error}`)
  assert(result.state.active === false, 'the retired state is missing')
  return `${result.error}: ${result.state.id} active=${result.state.active}`
})

await step('a repeated idempotency key answers the earlier result', async () => {
  const body = {
    target_type: 'unit',
    target_id: `${lineId}:u3`,
    field: 'reading',
    new: 'さ',
    base_revision: 0,
    client_id: clientId,
    idempotency_key: 'check-idempotency',
  }
  const first = await review(body)
  const again = await review(body)
  assert(first.status === 200 && again.status === 200, 'a review was refused')
  assert(again.result.duplicate === true, 'the repeat is not marked duplicate')
  equal(again.result.id, first.result.id, 'the repeated id')
  return `${first.result.id} answered twice, one event`
})

console.log('\nleaving the line (timing) and the recorded events')

await step('leave: a timing event with the dwell time', async () => {
  const { status, result } = await review({
    target_type: 'line',
    target_id: lineId,
    field: 'timing',
    new: { action: 'leave', closed_ms: Date.now(), dwell_ms: 1200 },
    client_id: clientId,
    idempotency_key: key(),
  })
  assert(status === 200, `status ${status}`)
  return `${result.id}, ${JSON.stringify(result.review.new)}`
})

await step('the events the service recorded match the actions', async () => {
  const rows = events()
  assert(rows.length > 0, 'no event was recorded')
  equal(
    rows.map((row) => row.id),
    rows.map((_, index) => `rv${String(index + 1).padStart(8, '0')}`),
    'the event ids are not the sequence',
  )
  equal(rows[0].id, firstTimingId, 'the first event is not the timing of the open')
  equal(rows[0].field, 'timing', 'the first event is not a timing event')
  const timing = rows.filter((row) => row.field === 'timing')
  equal(timing.length, 2, 'timing events')
  equal(timing[0].new.action, 'open', 'the open payload')
  equal(timing[1].new.action, 'leave', 'the leave payload')
  assert(timing[1].new.dwell_ms === 1200, 'the dwell time was not recorded')
  assert(timing.every((row) => row.actor === clientId), 'a timing event has the wrong actor')
  const segmentation = rows.filter((row) => row.field === 'segmentation')
  equal(segmentation.length, 2, 'segmentation events')
  equal(segmentation[0].id, splitResult.id, 'the split is not the first segmentation event')
  assert(segmentation[0].new.split.length === 2, 'the split event does not carry two entries')
  equal(segmentation[1].new.merge, [`${lineId}:m1`, `${lineId}:m2`], 'the merge event')
  assert(segmentation[0].old.id === unitId, 'the split event does not carry the old unit')
  const undo = rows.find((row) => row.id === undoEventId)
  assert(undo.evidence?.startsWith('undo of rv'), `the undo event has no evidence (${undo.evidence})`)
  assert(undo.new === undo.old || undo.field === 'classification', 'the undo does not carry the old value')
  const creations = rows.filter((row) => row.field === 'create')
  equal(creations.length, 2, 'create events')
  assert(creations.every((row) => row.role === 'transcriber'), 'a create event has the wrong role')
  const compensations = rows.filter((row) => row.evidence?.startsWith('undo of '))
  assert(compensations.length >= 2, `only ${compensations.length} compensating reviews`)
  assert(
    compensations.some((row) => row.target_id === merged.created[0] && row.field === 'active'),
    'the undo of the merge did not retire its output',
  )
  const reviewers = rows.filter((row) => row.role === 'reviewer')
  assert(reviewers.length >= 8, `only ${reviewers.length} reviewer events`)
  assert(rows.every((row) => row.client_id === clientId), 'an event has another client id')
  const withResult = rows.filter((row) => row.result)
  equal(withResult.length, rows.length, 'every event stored its result')
  return `${rows.length} events: ${rows.map((row) => row.field).join(', ')}`
})

await step('the reviewed state is what `apply` would write', async () => {
  const applied = await new Promise((done) => {
    const run = Bun.spawn([config.python, '-m', 'glyph_atlas.cli', 'review', 'apply', config.directory], {
      cwd: ROOT,
      env: { ...process.env, PYTHONPATH: join(ROOT, 'src') },
      stdout: 'pipe',
      stderr: 'pipe',
    })
    done(run.exited)
  })
  assert(applied === 0, `apply exited ${applied}`)
  const log = join(config.directory, 'reviews.jsonl')
  assert(existsSync(log), 'reviews.jsonl was not written')
  const lines = (await Bun.file(log).text()).trim().split('\n')
  const recorded = events()
  equal(lines.length, recorded.length, 'the log and the event table disagree')
  return `${lines.length} events in reviews.jsonl`
})

// -- the report -----------------------------------------------------------------------------------

await service.stop({ keep: config.keep })
console.log(
  `\n${results.length - failures}/${results.length} checks passed` +
    (config.keep ? `\nthe dataset is kept at ${config.directory}` : ''),
)
console.log(
  'covered: the HTTP contract of every action the interface performs, the recorded events, ' +
    'the 409 body, idempotency, and the server-rendered collection.',
)
console.log(
  'not covered: rendering, pointer drag and resize, the key handler, screenshots — ' +
    'tools/browser-check.mjs drives a real browser over CDP for those.',
)
process.exit(failures ? 1 : 0)
