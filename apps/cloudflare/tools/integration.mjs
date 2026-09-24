// Exercise the production Worker in workerd with real D1 transactions and R2 records.
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { Miniflare, convertV4MiniflareOptions } from 'miniflare'

const mf = new Miniflare(convertV4MiniflareOptions({workers:[{
  name: 'atlas-test',
  modules: true, script: await readFile('/tmp/atlas-worker-test.mjs', 'utf8'), compatibilityDate: '2026-09-22',
  d1Databases: ['DB'], r2Buckets: ['MEDIA'],
  serviceBindings: { ASSETS: () => new Response('assets') },
}]}))
try {
  const db = await mf.getD1Database('DB')
  // Every migration, in order, the way a new deployment applies them.
  const migrations = (await readdir(new URL('../migrations/', import.meta.url))).filter(name => name.endsWith('.sql')).sort()
  for (const name of migrations) {
    const schema = await readFile(new URL(`../migrations/${name}`, import.meta.url), 'utf8')
    const statements = schema.match(/CREATE TRIGGER[\s\S]*?\nEND;|(?:CREATE (?:TABLE|(?:UNIQUE )?INDEX)|DROP TRIGGER|UPDATE) [\s\S]*?;/g)
    await db.batch(statements.map(sql => db.prepare(sql)))
  }
  const hash = 'a'.repeat(64), sourceRevision = 'b'.repeat(64)
  for (const id of ['one', 'two']) {
    const d = { id, label: 'ア', reading: 'ア', state: 'pending', revision: 0, image_sha256: hash,
      production: 'manuscript', repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'ア', 'ア', 'U+3042', null, 'manuscript', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}').run()
  }
  for (const [char, code] of [['仮', 'U+4EEE'], ['假', 'U+5047']]) {
    const data = { char, code_point: code, grapheme: { code_point: 'U+4EEE' }, candidates: {} }
    await db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind(code, char, '', JSON.stringify(data), JSON.stringify(data)).run()
  }
  const corpus = { id: 'codh:fixture', origin: 'corpus', label: '仮', source_label: '仮', reading: '仮',
    written_character: null, identity_status: 'unassigned', grapheme: 'U+4EEE', visual_group: { id: 'group-one' },
    state: 'pending', revision: 0, proxyable: true, source_revision: sourceRevision }
  const raw = JSON.stringify(corpus)
  const bucket = await mf.getR2Bucket('MEDIA')
  await bucket.put('fixture', raw)
  await db.prepare('INSERT INTO corpus_units VALUES(?,?,?,?,?,?,?,?)').bind(corpus.id, null, 'U+4EEE', 'group-one', 1, 'fixture', 0, new TextEncoder().encode(raw).length).run()
  // Miniflare hands the Worker its own loopback address, so the page origin a browser would send is
  // that address; a fixed `http://localhost` fails the Worker's same-origin check on every POST.
  const base = new URL(await mf.ready).origin
  async function call(path, value, status = 200) {
    const response = await mf.dispatchFetch(base + path, value ? {
      method: 'POST', headers: { 'content-type': 'application/json', origin: base }, body: JSON.stringify(value),
    } : {})
    const json = await response.json()
    assert.equal(response.status, status, path + ' ' + JSON.stringify(json))
    return json
  }
  const decision = { id: 'one', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'merged', correction: 'アイ' }
  const round = { id: crypto.randomUUID(), client_id: 'integration', label: 'ア', answers: [decision] }
  const saved = await call('/atlas/rounds', round)
  assert.deepEqual(await call('/atlas/rounds', round), saved)
  assert.equal((await call('/atlas/characters/two')).state, 'pending', 'unselected is unjudged')
  assert.equal((await call('/atlas/characters/one')).revision, 1)
  await call('/atlas/rounds', { ...round, id: crypto.randomUUID(), answers: [
    { ...decision, id: 'two' }, { ...decision, revision: 0 },
  ] }, 409)
  assert.equal((await call('/atlas/characters/two')).revision, 0, 'stale rounds are atomic')
  await call(`/atlas/rounds/${round.id}/undo`, { client_id: 'integration' })
  const restored = await call('/atlas/characters/one')
  assert.equal(restored.state, 'pending')
  assert.equal(restored.revision, 2)
  const unassigned = '/layers/candidates?code_point=U%2B4EEE&scope=grapheme&visual_group=unassigned'
  assert.equal((await call(unassigned)).glyphs, 1, 'unlabeled visual groups are unassigned')
  const correction = { id: crypto.randomUUID(), client_id: 'integration', identity: corpus.id,
    source_revision: sourceRevision, revision: 0, verdict: 'wrong', issue: 'character', character: '假' }
  const fixed = await call('/atlas/corpus/reviews', correction)
  assert.equal(fixed.origin, 'corpus')
  assert.equal(fixed.label, '假')
  assert.equal(fixed.reading, '仮', 'written identity does not replace the source reading')
  assert.deepEqual(await call('/atlas/corpus/reviews', correction), fixed)
  assert.equal((await call(unassigned)).glyphs, 0)
  assert.equal((await call('/layers/candidates?code_point=U%2B5047')).glyphs, 1, 'corrected occurrence enters new search')
  assert.equal((await call('/layers/candidates?code_point=U%2B4EEE')).glyphs, 0)
  assert.equal((await call('/layers/gallery')).items[0].label, '假', 'gallery uses current reviews')
  const exported = await call('/atlas/reviews.json')
  assert.equal(exported.reviews.filter(r => r.current).length, 1)
  assert.equal(exported.reviews.find(r => r.origin === 'corpus').source_update.proposed_character, '假')
  assert.equal(exported.reviews.find(r => r.origin === 'corpus').source_update.original_character, '仮')
  assert.ok(exported.reviews.find(r => !r.origin).publication_snapshot.character)
  const cropProblem = { id: crypto.randomUUID(), client_id: 'integration', revision: 2,
    image_sha256: hash, verdict: 'wrong', issue: 'crop', character: '仮' }
  await call('/atlas/characters/one', cropProblem)
  assert.equal((await call('/atlas/characters/one')).state, 'flagged', 'text edits do not resolve bad geometry')
  assert.equal((await call('/atlas?group=kanji')).items[0].id, 'one', 'category follows the written identity')
  await call(`/atlas/rounds/${cropProblem.id}/undo`, { client_id: 'integration' })
  assert.equal((await call('/atlas?group=kana')).items.length, 2, 'undo restores the category')
  const ligature = { char: '𪜈', code_point: 'U+2A708', grapheme: { code_point: 'U+2A708' }, ligature: { reading: 'トモ' }, candidates: {} }
  await db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind('U+2A708', '𪜈', '', JSON.stringify(ligature), JSON.stringify(ligature)).run()
  const reading = { id: crypto.randomUUID(), client_id: 'integration', revision: 0,
    image_sha256: hash, verdict: 'wrong', issue: 'character', character: '𪜈', reading: 'とも' }
  await call('/atlas/characters/two', reading)
  assert.equal((await call('/atlas/characters/two')).reading, 'とも')
  const moved = { ...correction, id: crypto.randomUUID(), revision: 1, character: '𪜈' }
  await call('/atlas/corpus/reviews', moved)
  assert.equal((await call('/layers/candidates?code_point=U%2B2A708&scope=grapheme')).family_total, 1)
  assert.equal((await call('/layers/candidates?code_point=U%2B4EEE&scope=grapheme')).family_total, 0)
  // Seen crops: a round may record the crops it showed and left unflagged, and they leave the queue.
  for (const id of ['seen-a', 'seen-b', 'seen-c']) {
    const d = { id, label: 'セ', reading: 'セ', state: 'pending', revision: 0, image_sha256: hash,
      production: 'manuscript', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'セ', 'セ', 'U+30BB', null, 'manuscript', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}').run()
  }
  const pendingSe = async () => (await call('/atlas?purpose=review&reading=セ&state=pending&limit=96')).items.map(i => i.id).sort()
  const passed = { id: crypto.randomUUID(), client_id: 'integration', label: 'セ',
    seen: [{ id: 'seen-a', image_sha256: hash }, { id: 'seen-b', image_sha256: hash }, { id: 'seen-c', image_sha256: 'c'.repeat(64) }] }
  const recorded = await call('/atlas/rounds', passed)
  assert.equal(recorded.results.filter(r => r.field === 'seen').length, 2, 'a crop whose pixels changed is skipped')
  assert.deepEqual(await call('/atlas/rounds', passed), recorded, 'a retried pass is the same pass')
  const scrolled = { ...passed, seen: [...passed.seen, { id: 'seen-extra', image_sha256: hash }] }
  assert.deepEqual(await call('/atlas/rounds', scrolled), recorded, 'a retry with more crops on screen returns the first result')
  await call('/atlas/rounds', { id: crypto.randomUUID(), client_id: 'integration', seen: [{ id: 'seen-a', image_sha256: hash }] }, 422)
  assert.deepEqual(await pendingSe(), ['seen-c'], 'seen crops leave the queue')
  const summary = await call('/atlas?purpose=review&reading=セ')
  assert.equal(summary.counts.seen, 2)
  assert.equal(summary.items.find(i => i.id === 'seen-a').state, 'seen')
  assert.equal((await call('/atlas/characters/seen-a')).revision, 0, 'seeing a crop changes nothing about it')
  assert.ok(!(await call('/atlas/reviews.json?include_processed=true')).reviews.some(r => r.event?.target_id?.startsWith('seen-')), 'seen is not a review')
  const flagOnSeen = { id: crypto.randomUUID(), client_id: 'second', label: 'セ',
    answers: [{ id: 'seen-a', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'crop' }], seen: [{ id: 'seen-c', image_sha256: hash }] }
  await call('/atlas/rounds', flagOnSeen)
  assert.equal((await call('/atlas/characters/seen-a')).state, 'flagged', 'a seen crop can still be flagged at its revision')
  assert.deepEqual(await pendingSe(), [], 'answers and seen crops save together')
  // A flagged crop stays in the quiz and leads the next round until someone has seen it since.
  const dueSe = async () => (await call('/atlas?purpose=review&reading=セ&state=due&seed=3&limit=96'))
  let due = await dueSe()
  assert.deepEqual(due.items.map(i => i.id), ['seen-a'], 'a flagged crop is dealt again')
  assert.deepEqual([due.categories[0].due, due.categories[0].due_flagged], [1, 1])
  await call('/atlas/rounds', { id: crypto.randomUUID(), client_id: 'third', label: 'セ', seen: [{ id: 'seen-a', image_sha256: hash }] })
  due = await dueSe()
  assert.deepEqual(due.items, [], 'a flagged crop seen since its flag is not dealt again')
  assert.equal((await call('/atlas/characters/seen-a')).state, 'flagged', 'leaving a flagged crop unmarked keeps its flag')
  // Marking a flagged crop again is a look too, and undoing that round takes the look back.
  await db.prepare("INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)").bind('flag-b', 'local', 'セ', 'セ', 'U+30BB', null,
    'manuscript', 'kana', 'pending', 0, 1, 1, 1, JSON.stringify({ id: 'flag-b', label: 'セ', reading: 'セ', state: 'pending', revision: 0,
    image_sha256: hash, production: 'manuscript', box: { x: 1, y: 2, w: 3, h: 4 } }), '{}', '{}', '{}').run()
  const mark = (client, revision) => ({ id: crypto.randomUUID(), client_id: client, label: 'セ',
    answers: [{ id: 'flag-b', revision, image_sha256: hash, verdict: 'wrong', issue: 'crop' }] })
  await call('/atlas/rounds', mark('fourth', 0))
  assert.ok((await dueSe()).items.some(i => i.id === 'flag-b'), 'a newly flagged crop is due for another look')
  const agree = mark('fifth', 1)
  await call('/atlas/rounds', agree)
  assert.ok(!(await dueSe()).items.some(i => i.id === 'flag-b'), 'marking a flagged crop again is a look')
  await call(`/atlas/rounds/${agree.id}/undo`, { client_id: 'fifth' })
  assert.ok((await dueSe()).items.some(i => i.id === 'flag-b'), 'undoing the round takes the look back')
  await call(`/atlas/rounds/${passed.id}/undo`, { client_id: 'integration' })
  assert.deepEqual(await pendingSe(), ['seen-b'], 'undoing a pass returns its crops to the queue')
  assert.deepEqual((await dueSe()).items.map(i => i.id), ['flag-b', 'seen-b'], 'the queue of due crops follows the undo, flagged first')
  await call('/atlas/rounds', { id: crypto.randomUUID(), client_id: 'integration', label: 'セ', answers: [], seen: [] }, 422)
  console.log('Workerd integration passed: atomic rounds, issue-only saves, retries, undo, corpus identity, search, gallery, export, seen crops.')
} finally {
  await mf.dispose()
}
