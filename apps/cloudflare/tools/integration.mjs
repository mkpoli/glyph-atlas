// Exercise the production Worker in workerd with real D1 transactions and R2 records.
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { Miniflare, convertV4MiniflareOptions } from 'miniflare'

const mf = new Miniflare(convertV4MiniflareOptions({workers:[{
  name: 'atlas-test',
  modules: true, script: await readFile('/tmp/atlas-worker-test.mjs', 'utf8'), compatibilityDate: '2026-09-22',
  d1Databases: ['DB'], r2Buckets: ['MEDIA'],
}]}))
try {
  const db = await mf.getD1Database('DB')
  // Every migration, in order, the way a new deployment applies them. Rows published and reviewed
  // before 0006 are written first, to show what it makes of them.
  const migrations = (await readdir(new URL('../migrations/', import.meta.url))).filter(name => name.endsWith('.sql')).sort()
  const apply = async name => {
    const schema = await readFile(new URL(`../migrations/${name}`, import.meta.url), 'utf8')
    // Comments go first: a comment line that starts with a keyword would otherwise read as a statement.
    const statements = schema.replace(/^\s*--.*$/gm, '').match(/CREATE TRIGGER[\s\S]*?\nEND;|(?:CREATE (?:TABLE|(?:UNIQUE )?INDEX)|DROP TRIGGER|UPDATE|ALTER TABLE|DELETE FROM|INSERT INTO) [\s\S]*?;/g)
    await db.batch(statements.map(sql => db.prepare(sql)))
  }
  for (const name of migrations.filter(name => name < '0006')) await apply(name)
  const legacyBucket = await mf.getR2Bucket('MEDIA')
  let legacyPack = ''
  for (const [id, shuffle] of [['codh-omt:1', 77], ['codh-omtz:1', 78], ['codh:legacy', 79]]) {
    const bytes = JSON.stringify({ id, origin: 'corpus', label: 'ト', written_character: 'ト', proxyable: true, state: 'pending', revision: 0 })
    await db.prepare('INSERT INTO corpus_units VALUES(?,?,?,?,?,?,?,?)').bind(id, 'ト', null, null, shuffle, 'legacy',
      new TextEncoder().encode(legacyPack).length, new TextEncoder().encode(bytes).length).run()
    legacyPack += bytes
  }
  await legacyBucket.put('legacy', legacyPack)
  const reviewed = { id: 'codh:legacy', origin: 'corpus', label: 'ト', written_character: 'ト', proxyable: true, state: 'checked', revision: 1 }
  await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind('codh:legacy', 'corpus', 'ト', null, null, null,
    'unknown', 'other', 'checked', 1, 0, 1, 0, JSON.stringify(reviewed), JSON.stringify(reviewed), '{}', '{}').run()
  for (const name of migrations.filter(name => name >= '0006' && name < '0010')) await apply(name)
  // A crop published before 0010 carries an old production value in its row, its data and its snapshot.
  const oldPrint = { id: 'old-print', label: 'ト', reading: 'ト', state: 'pending', revision: 0, production: 'woodblock', production_label: 'Woodblock' }
  await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind('old-print', 'local', 'ト', 'ト', null, null,
    'woodblock', 'kana', 'pending', 0, 0, 1, 1, JSON.stringify(oldPrint), JSON.stringify({ character: oldPrint }), '{}', '{}').run()
  for (const name of migrations.filter(name => name >= '0010')) await apply(name)
  assert.deepEqual((await db.prepare("SELECT id,production,named FROM corpus_units WHERE character='ト' ORDER BY id").all()).results, [
    { id: 'codh-omt:1', production: 'printed/type', named: 0 },
    { id: 'codh-omtz:1', production: 'unknown', named: 0 },
    { id: 'codh:legacy', production: 'unknown', named: 1 }], 'the old movable-type set is marked, and a reviewed glyph is named')
  assert.deepEqual((await db.prepare("SELECT * FROM corpus_characters WHERE character='ト'").all()).results, [
    { character: 'ト', production: 'printed/type', n: 1, named: 0 }, { character: 'ト', production: 'unknown', n: 2, named: 1 }])
  assert.deepEqual(await db.prepare(`SELECT production,json_extract(data,'$.production') AS data,json_extract(data,'$.production_label') AS label,
    json_extract(snapshot,'$.character.production') AS snapshot FROM units WHERE id='old-print'`).first(),
    { production: 'printed', data: 'printed', label: 'Printed', snapshot: 'printed' }, 'a woodblock crop reads as printed everywhere')
  await db.prepare("DELETE FROM units WHERE id='old-print'").run()
  assert.deepEqual(await db.prepare("SELECT quiz,category,shuffle FROM units WHERE id='codh:legacy'").first(),
    { quiz: 1, category: 'kana', shuffle: 79 }, 'a reviewed corpus row gets the quiz, category and shuffle the Worker gives one')
  const hash = 'a'.repeat(64), sourceRevision = 'b'.repeat(64)
  for (const id of ['one', 'two']) {
    const d = { id, label: 'ア', reading: 'ア', state: 'pending', revision: 0, image_sha256: hash,
      production: 'handwritten', repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'ア', 'ア', 'U+3042', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  for (const [char, code] of [['仮', 'U+4EEE'], ['假', 'U+5047']]) {
    const data = { char, code_point: code, grapheme: { code_point: 'U+4EEE' }, candidates: {} }
    await db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind(code, char, '', JSON.stringify(data), JSON.stringify(data)).run()
  }
  const katakanaA = { char: 'ア', code_point: 'U+30A2', grapheme: { code_point: 'U+3042' }, candidates: {} }
  await db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind('U+30A2', 'ア', '', JSON.stringify(katakanaA), JSON.stringify(katakanaA)).run()
  const corpus = { id: 'codh:fixture', origin: 'corpus', label: '仮', source_label: '仮', reading: '仮',
    written_character: null, identity_status: 'unassigned', grapheme: 'U+4EEE', visual_group: { id: 'group-one' },
    state: 'pending', revision: 0, proxyable: true, source_revision: sourceRevision }
  const raw = JSON.stringify(corpus)
  const bucket = await mf.getR2Bucket('MEDIA')
  await bucket.put('fixture', raw)
  await db.prepare('INSERT INTO corpus_units VALUES(?,?,?,?,?,?,?,?,?,?)').bind(corpus.id, null, 'U+4EEE', 'group-one', 1, 'fixture', 0, new TextEncoder().encode(raw).length, 'unknown', 0).run()
  // The gallery deals copies of the published records; one copied from a pack a later publication
  // replaced no longer matches its row, and is not dealt.
  const stale = { ...corpus, id: 'codh:stale', label: '古' }
  await db.batch([db.prepare('INSERT INTO corpus_gallery VALUES(?,?,?,?,?)').bind(corpus.id, 1, 'fixture', 0, raw),
    db.prepare('INSERT INTO corpus_units VALUES(?,?,?,?,?,?,?,?,?,?)').bind(stale.id, null, 'U+53E4', null, 2, 'newer-pack', 0, 10, 'unknown', 0),
    db.prepare('INSERT INTO corpus_gallery VALUES(?,?,?,?,?)').bind(stale.id, 2, 'older-pack', 0, JSON.stringify(stale))])
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
  const galleried = (await call('/layers/gallery')).items
  assert.equal(galleried[0].label, '假', 'gallery uses current reviews')
  assert.ok(!galleried.some(item => item.id === stale.id), 'a copy of a replaced record is not dealt')
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
  // …and the family: the correction filed `one` under 仮's U+4EEE, and its undo files it back under ア's.
  const familyOf = async id => (await db.prepare('SELECT family FROM units WHERE id=?').bind(id).first()).family
  assert.equal(await familyOf('one'), 'U+3042', 'undo restores the family')
  await db.prepare('INSERT INTO unit_shapes VALUES(?,?)').bind('two', 7).run()
  const shaped = Object.fromEntries((await call('/atlas?purpose=review&production=all')).items.map(i => [i.id, i.shape_order]))
  assert.deepEqual(shaped, { one: null, two: 7 }, 'a crop carries its shape order, or null without one')
  await db.batch([db.prepare('INSERT INTO unit_suspects VALUES(?,?,?,?,?)').bind('two', 0.01, 'マ', 'ア', null),
    db.prepare('INSERT INTO unit_suspects VALUES(?,?,?,?,?)').bind('one', 0.02, null, 'イ', null)])
  const marked = Object.fromEntries((await call('/atlas?purpose=review&production=all')).items.map(i => [i.id, i.suspect]))
  assert.deepEqual(marked, { one: null, two: { p: 0.01, reads_as: 'マ' } },
    'a crop carries the classifier\'s doubt, or null without one or once relabelled')
  const browsed = Object.fromEntries((await call('/atlas')).items.map(i => [i.id, i.suspect]))
  assert.deepEqual(browsed, marked, 'browse carries the same doubt')
  // Flagged order: a crop already reviewed in the character inspector queues behind one nobody has.
  for (const id of ['flag-a', 'flag-b']) {
    const d = { id, label: 'ラ', reading: 'ラ', state: 'pending', revision: 0, image_sha256: hash,
      production: 'handwritten', repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'ラ', 'ラ', 'U+3042', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const flagRound = { id: crypto.randomUUID(), client_id: 'integration', label: 'ラ', answers: [
    { id: 'flag-a', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'blank' },
    { id: 'flag-b', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'blank' },
  ] }
  // A document's characters come in source order; a published unit shows its current review, and an
  // occurrence the site does not publish keeps the label it was published with.
  const occurrence = (page, position, label) => JSON.stringify({ sample: `${page}-l1-${position}`, page, line: 1, block: 'l1',
    position, context: 'ラア', source: 'transcription', label, box: [1, 2, 3, 4] })
  await db.batch([['hk:doc', 1, 'flag-a', occurrence(1, 1, 'ラ')], ['hk:doc', 0, 'ar:doc:1-l1-0', occurrence(1, 0, 'ア')],
    ['hk:other', 0, 'two', occurrence(1, 0, 'ア')]].map(row => db.prepare('INSERT INTO document_characters VALUES(?,?,?,?)').bind(...row)))
  const listed = async () => {
    const response = await mf.dispatchFetch(base + '/atlas/documents/hk%3Adoc/characters')
    assert.equal(response.status, 200)
    assert.equal(response.headers.get('access-control-allow-origin'), '*', 'other sites read it from the browser')
    assert.equal(response.headers.get('cache-control'), 'no-cache', 'a browser asks again, so a review shows')
    return (await response.json()).characters
  }
  const before = await listed()
  assert.deepEqual(await listed(), before, 'the edge copy answers the same, with the same headers')
  assert.deepEqual(before.map(c => [c.unit, c.atlas, c.label, c.state ?? null]),
    [['ar:doc:1-l1-0', false, 'ア', null], ['flag-a', true, 'ラ', 'pending']])
  await call('/atlas/rounds', flagRound)
  const after = (await listed())[1]
  assert.deepEqual([after.state, after.issue, after.revision], ['flagged', 'blank', 1], 'a review shows at once, past the cache')
  for (const missing of ['hk%3Anone', '%E0']) {
    const response = await mf.dispatchFetch(base + `/atlas/documents/${missing}/characters`)
    assert.deepEqual([response.status, response.headers.get('access-control-allow-origin')], [404, '*'], 'a page can tell a missing document apart')
  }
  const flaggedOrder = async () => (await call('/atlas?reading=ラ&state=flagged')).items.map(i => i.id)
  assert.deepEqual(await flaggedOrder(), ['flag-a', 'flag-b'], 'flagged crops nobody has reviewed keep their shuffled order')
  const inspected = { id: crypto.randomUUID(), client_id: 'inspector', revision: 1,
    image_sha256: hash, verdict: 'wrong', issue: 'crop' }
  await call('/atlas/characters/flag-a', inspected)
  assert.deepEqual(await flaggedOrder(), ['flag-b', 'flag-a'], 'a crop reviewed in the inspector moves behind one nobody has looked at')
  // `reported=hide` leaves the crop reviewed in the inspector out, and counts it; `reported=show`,
  // the default, leaves every other caller unaffected.
  const flagged = async (reported) => call('/atlas?reading=ラ&state=flagged' + (reported ? `&reported=${reported}` : ''))
  assert.equal((await flagged()).reported_count, 1, 'reported=show is the default, and counts what it would hide')
  assert.equal((await flagged('show')).reported_count, 1)
  const hiddenView = await flagged('hide')
  assert.deepEqual(hiddenView.items.map(i => i.id), ['flag-b'], 'reported=hide leaves the reviewed crop out')
  assert.equal(hiddenView.total, 1)
  assert.equal(hiddenView.reported_count, 1)
  await call(`/atlas/rounds/${inspected.id}/undo`, { client_id: 'inspector' })
  assert.deepEqual(await flaggedOrder(), ['flag-a', 'flag-b'], 'undoing the inspector review restores the order')
  assert.equal((await flagged('hide')).reported_count, 0, 'undoing the review brings the crop back')
  assert.deepEqual((await flagged('hide')).items.map(i => i.id).sort(), ['flag-a', 'flag-b'])
  const ligature = { char: '𪜈', code_point: 'U+2A708', grapheme: { code_point: 'U+2A708' }, ligature: { reading: 'トモ' }, candidates: {} }
  await db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind('U+2A708', '𪜈', '', JSON.stringify(ligature), JSON.stringify(ligature)).run()
  const reading = { id: crypto.randomUUID(), client_id: 'integration', revision: 0,
    image_sha256: hash, verdict: 'wrong', issue: 'character', character: '𪜈', reading: 'とも' }
  await call('/atlas/characters/two', reading)
  assert.equal((await call('/atlas/characters/two')).reading, 'とも')
  const renamed = (await call('/atlas/documents/hk%3Aother/characters')).characters[0]
  assert.deepEqual([renamed.label, renamed.source], ['𪜈', 'review'], 'a label corrected on the site is the review\'s')
  const moved = { ...correction, id: crypto.randomUUID(), revision: 1, character: '𪜈' }
  await call('/atlas/corpus/reviews', moved)
  assert.equal((await call('/layers/candidates?code_point=U%2B2A708&scope=grapheme')).family_total, 1)
  assert.equal((await call('/layers/candidates?code_point=U%2B4EEE&scope=grapheme')).family_total, 0)
  // Seen crops: a round may record the crops it showed and left unflagged, and they leave the queue.
  for (const id of ['seen-a', 'seen-b', 'seen-c']) {
    const d = { id, label: 'セ', reading: 'セ', state: 'pending', revision: 0, image_sha256: hash,
      image: `/atlas/media/${id}.webp`, production: 'handwritten', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'セ', 'セ', 'U+30BB', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  // Browse counts are cached per version of the data, so each write below must show in them at once.
  const browseSe = async () => { const { pending, seen, flagged } = (await call('/atlas')).categories.find(c => c.label === 'セ'); return { pending, seen, flagged } }
  assert.deepEqual(await browseSe(), { pending: 3, seen: 0, flagged: 0 })
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
  assert.deepEqual(await browseSe(), { pending: 1, seen: 2, flagged: 0 }, 'a round shows in the browse counts')
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
  assert.deepEqual(await browseSe(), { pending: 0, seen: 2, flagged: 1 })
  await call(`/atlas/rounds/${passed.id}/undo`, { client_id: 'integration' })
  assert.deepEqual(await pendingSe(), ['seen-b'], 'undoing a pass returns its crops to the queue')
  assert.deepEqual(await browseSe(), { pending: 1, seen: 1, flagged: 1 }, 'undoing a round with no reviews shows in the browse counts')
  await call('/atlas/rounds', { id: crypto.randomUUID(), client_id: 'integration', label: 'セ', answers: [], seen: [] }, 422)
  // A crop re-cut after the round was dealt shows another image; the reader never saw that one.
  const recut = await call('/atlas/rounds', { id: crypto.randomUUID(), client_id: 'integration', label: 'セ',
    seen: [{ id: 'seen-b', image_sha256: hash, image: '/atlas/media/an-older-cut.webp' }] })
  assert.equal(recut.results.filter(r => r.field === 'seen').length, 0, 'a crop re-cut since the round was dealt is not seen')
  const dealt = await call('/atlas/rounds', { id: crypto.randomUUID(), client_id: 'integration', label: 'セ',
    seen: [{ id: 'seen-b', image_sha256: hash, image: '/atlas/media/seen-b.webp' }] })
  assert.equal(dealt.results.filter(r => r.field === 'seen').length, 1, 'the crop the round showed is seen')
  // Skipped crops: recorded against the reviewer, dealt first to others, rested for the one who
  // skipped, hard once two reviewers skipped them, and taken back by an undo.
  for (const id of ['skip-a', 'skip-b', 'skip-c']) {
    const d = { id, label: 'ソ', reading: 'ソ', state: 'pending', revision: 0, image_sha256: hash,
      production: 'handwritten', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'ソ', 'ソ', 'U+30BD', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const dealtTo = async reviewer => (await call(`/atlas?purpose=review&reading=ソ&state=pending&seed=3&limit=96&reviewer=${reviewer}`)).items.map(i => i.id)
  const skipBy = (reviewer, id) => ({ id: crypto.randomUUID(), client_id: reviewer, label: 'ソ', skipped: [{ id, image_sha256: hash }] })
  await call('/atlas/rounds', skipBy('alice', 'skip-b'))
  assert.ok(!(await dealtTo('alice')).includes('skip-b'), 'a skip rests for the reviewer who made it')
  // A reviewer's own skips show only in their counts, whichever request comes first.
  const skippedSo = async (query = '') => (await call('/atlas' + query)).categories.find(c => c.label === 'ソ').skipped
  assert.equal(await skippedSo(), 0)
  assert.equal(await skippedSo('?reviewer=alice'), 1, 'a reviewer sees their own skip')
  assert.equal(await skippedSo(), 0, 'nobody else sees it in the browse counts')
  assert.equal((await dealtTo('bob'))[0], 'skip-b', 'another reviewer is dealt a skipped crop first')
  assert.equal((await call('/atlas/characters/skip-b')).state, 'pending', 'a skip changes nothing about the crop')
  const second = skipBy('bob', 'skip-b')
  await call('/atlas/rounds', second)
  assert.deepEqual((await call('/atlas?state=hard&reading=ソ')).items.map(i => i.id), ['skip-b'], 'two skips make a crop hard')
  assert.ok((await call('/atlas?state=attention&reading=ソ')).items.some(i => i.id === 'skip-b'), 'the Flagged view lists hard crops')
  assert.ok(!(await dealtTo('carol')).includes('skip-b'), 'a hard crop leaves the rounds')
  await call(`/atlas/rounds/${second.id}/undo`, { client_id: 'bob' })
  assert.deepEqual((await call('/atlas?state=hard&reading=ソ')).items, [], 'an undo takes a skip back')
  // Corpus glyphs in Quick review: a character's local crops first, then its assigned, proxyable
  // corpus glyphs, until a round names them and they become `units` rows like any other crop.
  const worker = await import('/tmp/atlas-worker-test.mjs')
  const glyph = (id, fields = {}) => ({ id, origin: 'corpus', label: 'ナ', char: 'ナ', written_character: 'ナ',
    identity_status: 'assigned', source_label: 'ナ', reading: 'ナ', grapheme: 'U+30CA', state: 'pending', revision: 0,
    proxyable: true, production: 'printed/woodblock', image: `/atlas/media/${id}.webp`, box: { x: 1, y: 2, w: 3, h: 4 },
    source: { corpus: 'codh-full', title: 'A woodblock book' }, source_revision: createHash('sha256').update(id).digest('hex'), ...fields })
  // shuffle 50,10,40,20,30: from seed 0 the order is na-2, na-4, na-5, na-3, na-1.
  const glyphs = [['na-1', 50], ['na-2', 10], ['na-3', 40], ['na-4', 20], ['na-5', 30]].map(([id, shuffle]) => [glyph(id), shuffle, 'ナ'])
  glyphs.push([glyph('na-movable', { production: 'printed/type/wood' }), 15, 'ナ'],
    [glyph('na-unassigned', { written_character: null, identity_status: 'unassigned' }), 5, null],
    [glyph('nu-private', { label: 'ヌ', char: 'ヌ', written_character: 'ヌ', proxyable: false }), 5, 'ヌ'],
    [glyph('nu-shown', { label: 'ヌ', char: 'ヌ', written_character: 'ヌ' }), 6, 'ヌ'])
  let packed = ''
  for (const [record, shuffle, character] of glyphs) {
    const bytes = JSON.stringify(record), offset = new TextEncoder().encode(packed).length
    packed += bytes
    await db.prepare('INSERT INTO corpus_units VALUES(?,?,?,?,?,?,?,?,?,?)').bind(record.id, character, 'U+30CA', null, shuffle,
      'pack-na', offset, new TextEncoder().encode(bytes).length, record.production, 0).run()
  }
  await bucket.put('pack-na', packed)
  // The publication scripts restore `named` and regenerate the counts with the migration's own statements.
  const migration = await readFile(new URL('../migrations/0006_corpus_rounds.sql', import.meta.url), 'utf8')
  const refresh = migration.match(/UPDATE corpus_units SET named=1 WHERE id IN [\s\S]*?;|DELETE FROM corpus_characters;|INSERT INTO corpus_characters [\s\S]*?;/g)
  assert.equal(refresh.length, 3)
  await db.batch(refresh.map(sql => db.prepare(sql)))
  for (const id of ['na-local-a', 'na-local-b']) {
    const d = { id, label: 'ナ', reading: 'ナ', state: 'pending', revision: 0, image_sha256: hash,
      production: 'handwritten', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'ナ', 'ナ', 'U+30CA', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const roundOf = async (params = '') => call(`/atlas?purpose=review&reading=ナ&state=pending${params.includes('limit=') ? '' : '&limit=96'}${params}`)
  const ids = async params => (await roundOf(params)).items.map(i => i.id)
  const dealtNa = await ids('&seed=0')
  assert.deepEqual(dealtNa.slice(0, 2).sort(), ['na-local-a', 'na-local-b'], 'local crops come first')
  assert.deepEqual(dealtNa.slice(2), ['na-2', 'na-4', 'na-5', 'na-3', 'na-1'], 'then corpus glyphs in shuffle order')
  assert.deepEqual((await ids('&seed=25')).slice(2), ['na-5', 'na-3', 'na-1', 'na-2', 'na-4'], 'the seed picks where the shuffle starts')
  await db.batch([db.prepare('INSERT INTO unit_suspects VALUES(?,?,?,?,?)').bind('na-3', 0.02, null, 'ナ', '{"x":1,"y":2,"w":3,"h":4}'),
    db.prepare('INSERT INTO unit_suspects VALUES(?,?,?,?,?)').bind('na-1', 0.02, null, 'ナ', '{"x":1,"y":2,"w":3,"h":9}')])
  const doubted = Object.fromEntries((await roundOf('&seed=0')).items.map(i => [i.id, i.suspect]))
  assert.deepEqual(doubted['na-3'], { p: 0.02, reads_as: null }, 'a corpus glyph carries its mark as well')
  assert.equal(doubted['na-1'], null, 'a mark made for another box does not hold')
  const paged = []
  for (let offset = 0; offset < 7; offset += 3) paged.push(...await ids(`&seed=0&limit=3&offset=${offset}`))
  assert.deepEqual(paged, dealtNa, 'paging runs on from the local crops into the corpus glyphs')
  assert.equal((await roundOf('&seed=0')).total, 7)
  await call('/atlas?purpose=review&reading=ナ&offset=5000', undefined, 404)
  assert.ok(!dealtNa.includes('na-movable') && !dealtNa.includes('na-unassigned'), 'movable type and unassigned glyphs are not dealt')
  assert.ok((await ids('&seed=0&production=all')).includes('na-movable'), 'every material includes movable type')
  assert.deepEqual((await ids('&seed=0&production=printed/woodblock')), ['na-2', 'na-4', 'na-5', 'na-3', 'na-1'], 'one material deals only its glyphs')
  assert.deepEqual((await ids('&seed=0&production=printed/type')), ['na-movable'], 'a node deals what lies under it')
  assert.deepEqual((await ids('&seed=0&production=printed')), ['na-2', 'na-movable', 'na-4', 'na-5', 'na-3', 'na-1'], 'a wider node merges its productions in shuffle order')
  assert.deepEqual((await ids('&seed=0&production=inscribed')), [], 'a node the character has nothing under deals nothing')
  await call('/atlas?purpose=review&reading=ナ&production=printed%20type', undefined, 400)
  await call('/atlas?purpose=review&reading=ナ&production=not:all', undefined, 400)
  await call('/atlas?purpose=review&reading=ナ&production=printed/typo', undefined, 400)
  assert.deepEqual((await call('/atlas?purpose=review&reading=ヌ&state=pending')).items.map(i => i.id), ['nu-shown'], 'a glyph this site may not serve is not dealt')
  // A glyph passed over still takes its position: the next offset runs ahead of the items, and once
  // the glyphs run out the total is what there was to deal.
  const passedOver = await call('/atlas?purpose=review&reading=ヌ&state=pending&limit=1')
  assert.deepEqual([passedOver.items.length, passedOver.next_offset, passedOver.total], [0, 1, 2])
  const rest = await call('/atlas?purpose=review&reading=ヌ&state=pending&limit=2&offset=1')
  assert.deepEqual([rest.items.map(i => i.id), rest.next_offset, rest.total], [['nu-shown'], 2, 2])
  const first = (await roundOf('&seed=0')).items.find(i => i.id === 'na-2')
  assert.equal(first.origin, 'corpus')
  assert.equal(first.source.title, 'A woodblock book', 'a corpus tile can name its source')
  const category = async (reviewer = '') => (await call(`/atlas?purpose=review&limit=1${reviewer}`)).categories.find(c => c.label === 'ナ')
  assert.deepEqual(await category(), { label: 'ナ', grapheme: 'U+30CA', total: 7, pending: 7, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 }, 'counts include corpus glyphs')
  assert.equal((await call('/atlas?purpose=review&limit=1&production=all')).categories.find(c => c.label === 'ナ').pending, 8)
  const na = Object.fromEntries((await roundOf('&seed=0')).items.map(i => [i.id, i]))
  const cropRound = { id: crypto.randomUUID(), client_id: 'alice', label: 'ナ',
    answers: [{ id: 'na-2', revision: 0, source_revision: na['na-2'].source_revision, verdict: 'wrong', issue: 'crop' }],
    seen: [{ id: 'na-4', source_revision: na['na-4'].source_revision, image: na['na-4'].image }],
    skipped: [{ id: 'na-5', source_revision: na['na-5'].source_revision, image: na['na-5'].image }] }
  const cropSaved = await call('/atlas/rounds', cropRound)
  assert.deepEqual(cropSaved.results.map(r => r.field), ['review', 'seen', 'skip'])
  assert.deepEqual(await call('/atlas/rounds', cropRound), cropSaved, 'a retried corpus round is the same round')
  const aliceIds = await ids('&seed=0&reviewer=alice')
  assert.deepEqual(aliceIds.slice(2), ['na-3', 'na-1'], 'flagged, seen and skipped glyphs leave the next round')
  assert.equal((await ids('&seed=0&reviewer=bob'))[0], 'na-5', 'another reviewer is dealt a skipped corpus glyph first')
  // Counts cover local crops and untouched glyphs; named corpus glyphs are no longer counted anywhere.
  assert.deepEqual(await category('&reviewer=alice'), { label: 'ナ', grapheme: 'U+30CA', total: 4, pending: 4, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 })
  assert.deepEqual((await db.prepare("SELECT id FROM corpus_units WHERE character='ナ' AND named=1 ORDER BY id").all()).results.map(r => r.id),
    ['na-2', 'na-4', 'na-5'], 'naming a glyph marks its published row')
  assert.deepEqual(await db.prepare("SELECT n,named FROM corpus_characters WHERE character='ナ' AND production='printed/woodblock'").first(), { n: 5, named: 3 })
  assert.equal((await call('/atlas/rounds', cropRound)).id, cropSaved.id)
  assert.equal((await db.prepare("SELECT named FROM corpus_characters WHERE character='ナ' AND production='printed/woodblock'").first()).named, 3, 'a retried round names nothing twice')
  const materialised = await db.prepare("SELECT id,origin,quiz,state,shuffle FROM units WHERE id LIKE 'na-%' AND origin='corpus' ORDER BY id").all()
  assert.deepEqual(materialised.results, [
    { id: 'na-2', origin: 'corpus', quiz: 1, state: 'flagged', shuffle: 10 },
    { id: 'na-4', origin: 'corpus', quiz: 1, state: 'pending', shuffle: 20 },
    { id: 'na-5', origin: 'corpus', quiz: 1, state: 'pending', shuffle: 30 }], 'a round writes the units rows it names')
  assert.equal((await call('/atlas/corpus/character?id=na-2')).state, 'flagged')
  await call(`/atlas/rounds/${cropRound.id}/undo`, { client_id: 'alice' })
  const undone = await ids('&seed=0&reviewer=alice')
  assert.deepEqual(undone.slice(0, 2).sort(), ['na-local-a', 'na-local-b'], 'local crops still come first')
  assert.deepEqual(undone.slice(2).sort(), ['na-1', 'na-2', 'na-3', 'na-4', 'na-5'], 'undo returns the glyphs to the round')
  assert.equal((await roundOf('&seed=0&reviewer=alice')).total, 7, 'the round of their own character counts them again')
  // Should a named glyph's published row read as untouched, it is still dealt once.
  await db.prepare("UPDATE corpus_units SET named=0 WHERE id='na-2'").run()
  const once = await ids('&seed=0&reviewer=alice')
  assert.equal(once.length, new Set(once).size, 'a round never returns the same crop twice')
  assert.equal(once.filter(id => id === 'na-2').length, 1)
  await db.prepare("UPDATE corpus_units SET named=1 WHERE id='na-2'").run()
  assert.equal((await db.prepare("SELECT quiz FROM units WHERE id='na-2'").first()).quiz, 1, 'undo keeps a dealable glyph in the quiz')
  // A glyph the site may not serve cannot be named by a round, nor answered in one.
  const refused = await call('/atlas/rounds', { id: crypto.randomUUID(), client_id: 'alice', label: 'ヌ',
    seen: [{ id: 'nu-private', source_revision: createHash('sha256').update('nu-private').digest('hex') }] })
  assert.equal(refused.results.length, 0)
  await call('/atlas/rounds', { id: crypto.randomUUID(), client_id: 'alice', label: 'ナ', answers: [{ id: 'na-unassigned', revision: 0,
    source_revision: createHash('sha256').update('na-unassigned').digest('hex'), verdict: 'wrong', issue: 'crop' }] }, 409)
  // Every new query shape reads corpus_units through an index, in index order. Each check is shown to
  // fail once its index is gone.
  const plan = async ({ sql, values }, bound) => (await db.prepare('EXPLAIN QUERY PLAN ' + sql).bind(...bound, ...values).all()).results.map(r => r.detail)
  function served(details, index) {
    assert.ok(!details.some(d => /^SCAN (c|corpus_units)\b/.test(d) && !/USING (COVERING )?INDEX/.test(d)), details.join('; '))
    assert.ok(!details.some(d => d.includes('USE TEMP B-TREE FOR ORDER BY')), details.join('; '))
    // The index is named as a whole word: a search is followed by its terms, a full index walk by nothing.
    if (index) assert.ok(details.some(d => new RegExp(`USING (COVERING )?INDEX ${index}\\b`).test(d)), `${index}: ${details.join('; ')}`)
  }
  const shapes = []
  for (const production of [null, 'printed/woodblock'])
    for (const side of ['>=', '<'])
      shapes.push([{ sql: worker.corpusRoundQuery(production, side), values: [] }, ['ナ', ...(production ? [production] : []), 0, 96],
        production ? 'corpus_material' : 'corpus_round'])
  // corpus_characters is small and read whole, in its key's order.
  for (const production of ['all', 'not:printed/type', 'printed/woodblock']) shapes.push([worker.corpusCountQuery(production), [], null])
  shapes.push([{ sql: worker.namedRoundQuery('NOT (production>=? AND production<?)', 'state'), values: [] },
    ['ナ', 'printed/type', 'printed/type0'], 'unit_character'])
  // What a publication runs after it rewrites corpus_units, and what the trigger runs on each naming.
  shapes.push([{ sql: refresh[0], values: [] }, [], 'sqlite_autoindex_corpus_units_1'])
  shapes.push([{ sql: "UPDATE corpus_characters SET named=named+1 WHERE (character,production)=(SELECT character,production FROM corpus_units WHERE id=? AND named=0)", values: [] },
    ['na-1'], 'sqlite_autoindex_corpus_units_1'])
  // GET /atlas/history: all history newest first, by actor, and by label, each served by its own partial index.
  shapes.push([{ sql: worker.historyQuery(null, null, null).sql, values: [] }, [41], 'event_history'])
  shapes.push([{ sql: worker.historyQuery('integration', null, null).sql, values: [] }, ['integration', 41], 'event_actor_history'])
  shapes.push([{ sql: worker.historyQuery(null, 'ア', null).sql, values: [] }, ['ア', 41], 'event_label_history'])
  // The keyset cursor stays on the same index once a page is under way, for the plain and the actor shape.
  const cursor = { at: '2026-01-01T00:00:00.000Z', id: 'cf:0' }
  shapes.push([{ sql: worker.historyQuery(null, null, cursor).sql, values: [] }, [cursor.at, cursor.id, 41], 'event_history'])
  shapes.push([{ sql: worker.historyQuery('integration', null, cursor).sql, values: [] }, ['integration', cursor.at, cursor.id, 41], 'event_actor_history'])
  // Browsing one grapheme deals its crops from the seed's point in shuffle order, as `catalogue` asks.
  shapes.push([{ sql: "SELECT * FROM units WHERE origin='local' AND family=? AND shuffle>=? ORDER BY shuffle,rowid LIMIT ? OFFSET ?", values: [] },
    ['U+4EEE', 0, 60, 0], 'unit_family_sample'])
  // A round and its reference strips count their own character only, through its index; the review
  // filter off its index keeps the planner from walking every crop that can be dealt.
  const roundFilter = worker.listingFilter(true, 'not:printed/type', 'ナ')
  shapes.push([{ sql: worker.facetsQuery(true, 'state', roundFilter.where), values: [] }, roundFilter.values, 'unit_character'])
  shapes.push([worker.corpusCountQuery('not:printed/type', 'ナ'), [], null])
  // A document's characters are read along the table's own key, and each unit by its id.
  const documentPlan = await plan({ sql: worker.documentCharactersQuery(), values: [] }, ['hk:doc'])
  served(documentPlan, null)
  assert.ok(documentPlan.includes('SEARCH c USING PRIMARY KEY (document=?)'), documentPlan.join('; '))
  assert.ok(documentPlan.some(d => /^SEARCH u USING INDEX sqlite_autoindex_units_1 \(id=\?\)/.test(d)), documentPlan.join('; '))
  // `reported=hide`'s `NOT EXISTS` filter: the flagged set is small, but events and submissions are
  // still read through their own indexes, one correlated lookup per candidate row, never a scan.
  const reportedPlan = await plan({ sql: `SELECT count(*) AS n FROM units WHERE origin='local' AND state='flagged' AND NOT ${worker.reviewedInInspectorQuery()}`, values: [] }, [])
  assert.ok(!reportedPlan.some(d => /^SCAN \w+/.test(d) && !/USING (COVERING )?INDEX/.test(d)), reportedPlan.join('; '))
  assert.ok(reportedPlan.some(d => d.includes('SEARCH e USING INDEX event_target')), reportedPlan.join('; '))
  assert.ok(reportedPlan.some(d => /SEARCH f USING (COVERING )?INDEX sqlite_autoindex_submissions_1/.test(d)), reportedPlan.join('; '))
  for (const [shape, bound, index] of shapes) {
    const details = await plan(shape, bound)
    if (process.env.SHOW_PLANS) console.log(index, JSON.stringify(details))
    served(details, index)
  }
  const keys = {
    corpus_round: 'CREATE INDEX corpus_round ON corpus_units(character,named,shuffle)',
    corpus_material: 'CREATE INDEX corpus_material ON corpus_units(character,production,named,shuffle)',
    unit_character: 'CREATE INDEX unit_character ON units(origin,character,state)',
    unit_family_sample: 'CREATE INDEX unit_family_sample ON units(origin,family,shuffle)',
    event_history: "CREATE INDEX event_history ON events(at DESC, id DESC) WHERE kind IN ('review','undo')",
    event_actor_history: "CREATE INDEX event_actor_history ON events(actor, at DESC, id DESC) WHERE kind IN ('review','undo')",
    event_label_history: `CREATE INDEX event_label_history ON events(${worker.historyLabelExpr()}, at DESC, id DESC) WHERE kind IN ('review','undo')`,
  }
  for (const [index, create] of Object.entries(keys)) {
    await db.prepare(`DROP INDEX ${index}`).run()
    for (const [shape, bound] of shapes.filter(s => s[2] === index))
      await assert.rejects(async () => served(await plan(shape, bound), index), `the check on ${index} fails without it`)
    await db.prepare(create).run()
  }
  // A row published as `other` before Hangul had a category reads `hangul` once 0008 has run, and
  // the listing filters it by that group.
  const jamo = { id: 'hangul', label: 'ㅿ', reading: 'ㅿ', state: 'pending', revision: 0, image_sha256: hash,
    production: 'printed/woodblock', repair: { quiz: true } }
  await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
    jamo.id, 'local', 'ㅿ', 'ㅿ', 'U+317F', null, 'printed/woodblock', 'other', 'pending', 0, 1, 1, 1,
    JSON.stringify(jamo), JSON.stringify({ character: jamo }), '{}', '{}', null).run()
  await apply('0008_hangul_category.sql')
  assert.deepEqual((await call('/atlas?group=hangul')).items.map(i => i.id), ['hangul'], 'a Hangul label is in the hangul group')
  assert.ok(!(await call('/atlas?group=kana')).items.some(i => i.id === 'hangul'), 'and in no other')
  // A row published as `other` before gugyeol had a category reads `gugyeol` once 0012 has run, and
  // the listing filters it by that group.
  const gugyeol = { id: 'gugyeol', label: '', reading: '', state: 'pending', revision: 0, image_sha256: hash,
    production: 'printed/woodblock', repair: { quiz: true } }
  await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
    gugyeol.id, 'local', '', '', 'U+F67F', null, 'printed/woodblock', 'other', 'pending', 0, 1, 1, 1,
    JSON.stringify(gugyeol), JSON.stringify({ character: gugyeol }), '{}', '{}', null).run()
  await apply('0012_gugyeol_category.sql')
  assert.deepEqual((await call('/atlas?group=gugyeol')).items.map(i => i.id), ['gugyeol'], 'a gugyeol label is in the gugyeol group')
  assert.ok(!(await call('/atlas?group=kana')).items.some(i => i.id === 'gugyeol'), 'and in no other')
  // Explore narrows to one book: the listing counts crops per book, and `document` lists one book's.
  // A reviewer's listing is read uncached, so the rows written straight to D1 show in its counts.
  for (const [id, book, title] of [['book-a1', 'hl:A', '甲'], ['book-a2', 'hl:A', '甲'], ['book-b1', 'hl:B', '乙']]) {
    const d = { id, label: 'ヌ', reading: 'ヌ', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten',
      source: title, page_id: `${book}:1`, repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'ヌ', 'ヌ', 'U+30CC', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', book).run()
  }
  assert.deepEqual((await call('/atlas?document=hl:A&limit=96')).items.map(i => i.id).sort(), ['book-a1', 'book-a2'], 'one book lists its own crops')
  assert.deepEqual((await call('/atlas?reviewer=shelf')).documents.filter(b => b.id.startsWith('hl:')).map(({ id, title, total }) => ({ id, title, total })),
    [{ id: 'hl:A', title: '甲', total: 2 }, { id: 'hl:B', title: '乙', total: 1 }], 'the listing counts crops per book, with the title they were published under')
  // The listing files each label under its grapheme, and `grapheme` lists the whole family: 仮 and 假
  // under U+4EEE, and ※, which the character table gives no family, under its own code point, as the
  // publication writes it.
  for (const [id, label, family] of [['kari-1', '仮', 'U+4EEE'], ['kari-2', '假', 'U+4EEE'], ['mark', '※', 'U+203B']]) {
    const d = { id, label, reading: label, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten', repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', label, label, family, null, 'handwritten', 'kanji', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const filed = Object.fromEntries((await call('/atlas?reviewer=shelf')).categories.filter(c => ['仮', '假', '※'].includes(c.label)).map(c => [c.label, c.grapheme]))
  assert.deepEqual(filed, { '仮': 'U+4EEE', '假': 'U+4EEE', '※': 'U+203B' }, 'each label names its grapheme')
  const kari = (await call('/atlas?grapheme=U%2B4EEE&limit=96')).items
  assert.ok(['kari-1', 'kari-2'].every(id => kari.some(i => i.id === id)) && kari.every(i => ['仮', '假'].includes(i.label)),
    `a grapheme lists its whole family and nothing else: ${kari.map(i => i.id + ' ' + i.label)}`)
  assert.deepEqual((await call('/atlas?grapheme=u%2B203b')).items.map(i => i.id), ['mark'], 'a label with no family is filed under its own code point')
  assert.equal((await mf.dispatchFetch(base + '/atlas?grapheme=%E4%BB%AE')).status, 422, 'a grapheme is named by code points')
  // The migration names the label categories the Worker computes, code point by code point.
  const hangulMigration = await readFile(new URL('../migrations/0008_hangul_category.sql', import.meta.url), 'utf8')
  const gugyeolMigration = await readFile(new URL('../migrations/0012_gugyeol_category.sql', import.meta.url), 'utf8')
  const ranges = (kind, sql = migration) => [...sql.split(`THEN '${kind}'`)[0].split('WHEN').at(-1).matchAll(/c BETWEEN (0x[0-9A-F]+) AND (0x[0-9A-F]+)|c=(0x[0-9A-F]+)/g)]
    .map(m => m[3] ? [Number(m[3]), Number(m[3])] : [Number(m[1]), Number(m[2])])
  const kana = ranges('kana'), han = ranges('kanji'), hangul = ranges('hangul', hangulMigration), gugyeolRange = ranges('gugyeol', gugyeolMigration)
  const within = (list, c) => list.some(([a, b]) => a <= c && c <= b)
  for (let c = 0; c <= 0x10FFFF; c++) {
    if (c >= 0xD800 && c <= 0xDFFF) continue
    const sql = within(kana, c) ? 'kana' : within(han, c) ? 'kanji' : within(hangul, c) ? 'hangul'
      : within(gugyeolRange, c) ? 'gugyeol' : 'other'
    if (worker.categoryOf(String.fromCodePoint(c)) !== sql) assert.fail(`U+${c.toString(16)}: ${worker.categoryOf(String.fromCodePoint(c))} vs ${sql}`)
  }
  // A context widened in place survives a review and its undo.
  const framed = { id: 'framed', label: 'カ', reading: 'カ', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten',
    repair: { quiz: true }, context: true, context_image: '/atlas/media/narrow.webp', context_box: { x: 5, y: 5, w: 40, h: 60 } }
  await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
    framed.id, 'local', 'カ', 'カ', 'U+30AB', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
    JSON.stringify(framed), JSON.stringify({ character: framed }), '{}', '{}', null).run()
  const wide = { x: 0, y: 0, w: 90, h: 120 }
  const framedRound = { id: crypto.randomUUID(), client_id: 'integration', label: 'カ',
    answers: [{ id: 'framed', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'blank' }] }
  await call('/atlas/rounds', framedRound)
  await db.prepare(`UPDATE units SET data=json_set(data,'$.context_image',json('"/atlas/media/wide.webp"'),'$.context_box',json(?)) WHERE id='framed' AND revision=1`)
    .bind(JSON.stringify(wide)).run()
  await call(`/atlas/rounds/${framedRound.id}/undo`, { client_id: 'integration' })
  const unframed = await call('/atlas/characters/framed')
  assert.deepEqual([unframed.state, unframed.context_image, unframed.context_box], ['pending', '/atlas/media/wide.webp', wide],
    'undo restores the review state and keeps the wider context')
  // GET /atlas/history: every review and undo, newest first, filterable by actor or by label; a kind outside
  // ('review','undo') never appears even though its row sits in the same table.
  const get = async path => { const response = await mf.dispatchFetch(base + path); assert.equal(response.status, 200); return response.json() }
  const all = await get('/atlas/history?limit=100')
  assert.ok(all.items.every(i => ['review', 'undo'].includes(i.kind)), 'only review and undo rows are ever listed')
  const sorted = [...all.items].sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : (a.id < b.id ? 1 : -1)))
  assert.deepEqual(all.items.map(i => i.id), sorted.map(i => i.id), 'newest first, tied at ties broken by id')
  const byLabel = await get('/atlas/history?label=%E3%83%A9&limit=100')
  assert.equal(byLabel.items.length, 4, 'two flagged rounds, an inspector review and its undo all read ラ')
  assert.ok(byLabel.items.every(i => i.label === 'ラ'))
  const review = byLabel.items.find(i => i.kind === 'review' && i.actor === 'inspector')
  assert.deepEqual([review.verdict, review.issue, review.undoes], ['wrong', 'crop', null])
  const undoneEntry = byLabel.items.find(i => i.kind === 'undo')
  assert.deepEqual([undoneEntry.actor, undoneEntry.verdict, undoneEntry.character, undoneEntry.undoes], ['inspector', null, null, review.id],
    'an undo names the review it reverses and leaves the review-only fields null')
  const byActor = await get('/atlas/history?actor=inspector&limit=100')
  assert.equal(byActor.items.length, 2, 'the inspector review and its own undo')
  assert.ok(byActor.items.every(i => i.actor === 'inspector'))
  // Keyset pagination: a page of one, followed by `before`, walks the same list `limit=100` returned.
  const historyPage1 = await get('/atlas/history?actor=inspector&limit=1')
  assert.equal(historyPage1.items.length, 1)
  assert.ok(historyPage1.next, 'a further page is signalled')
  const historyPage2 = await get(`/atlas/history?actor=inspector&limit=1&before=${encodeURIComponent(historyPage1.next)}`)
  assert.equal(historyPage2.items.length, 1)
  assert.equal(historyPage2.next, null, 'the list ends once every actor row is read')
  assert.deepEqual([historyPage1.items[0].id, historyPage2.items[0].id], byActor.items.map(i => i.id), 'paging one at a time visits the same rows in the same order')
  const overLimit = await mf.dispatchFetch(base + '/atlas/history?limit=1000')
  assert.equal(overLimit.status, 422, 'limit is bounded at 100')
  const badCursor = await mf.dispatchFetch(base + '/atlas/history?before=not-a-cursor')
  assert.equal(badCursor.status, 422, 'an unreadable cursor is rejected rather than silently ignored')
  // Hosted form assignment: a published clustering, a named cluster, a glyph's own decision and
  // following the cluster again; search and the record follow each step.
  await db.batch([
    db.prepare("INSERT INTO form_families(code_point,char,label,count,cluster_count,forms,assigned,revision) VALUES('U+4EEE','仮','仮 = 假',2,1,?,0,'r1')").bind(JSON.stringify([{ char: '仮', code_point: 'U+4EEE' }, { char: '假', code_point: 'U+5047' }])),
    db.prepare("INSERT INTO form_clusters(id,family,label,count,coherence,shape_position,size_position,representatives) VALUES('U+4EEE:c1','U+4EEE','Cluster 1',2,0.9,0,0,'[]')"),
    db.prepare("INSERT INTO form_units(id,family,cluster,rank,similarity,image,split) VALUES('codh:plain','U+4EEE','U+4EEE:c1',0,0.95,NULL,'0000000')"),
    db.prepare("INSERT INTO form_units(id,family,cluster,rank,similarity,image,split) VALUES('codh:fixture','U+4EEE','U+4EEE:c1',1,0.9,NULL,'1111111')"),
  ])
  const plain = JSON.stringify({ ...corpus, id: 'codh:plain' })
  await bucket.put('plain', plain)
  await db.prepare('INSERT INTO corpus_units VALUES(?,?,?,?,?,?,?,?,?,?)').bind('codh:plain', '假', 'U+4EEE', null, 2, 'plain', 0, new TextEncoder().encode(plain).length, 'unknown', 0).run()
  await db.prepare('INSERT INTO corpus_gallery VALUES(?,?,?,?,?)').bind('codh:plain', 2, 'plain', 0, plain).run()
  await db.prepare("INSERT INTO corpus_characters VALUES('假','unknown',1,0) ON CONFLICT DO UPDATE SET n=n+1").run()
  // Quick review's per-character counts agree with a recount of the rows after every decision.
  const counted = async () => {
    const kept = (await db.prepare('SELECT character,production,n,named FROM corpus_characters ORDER BY 1,2').all()).results
    const recount = (await db.prepare('SELECT character,production,count(*) AS n,sum(named) AS named FROM corpus_units WHERE character IS NOT NULL GROUP BY 1,2 ORDER BY 1,2').all()).results
    assert.deepEqual(kept, recount, 'corpus_characters follows the forms')
  }
  await counted()
  assert.equal((await call('/atlas/forms/families')).items[0].code_point, 'U+4EEE')
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: 'あ', client_id: 'integration' }, 422)
  const named = await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: '仮', client_id: 'integration' })
  assert.equal(named.count, 2)
  const family = await call('/atlas/forms/families/U%2B4EEE')
  assert.deepEqual([family.assigned, family.items[0].form, family.items[0].assigned, family.items[0].majority, family.items[0].majority_count], [2, '仮', 2, '仮', 2])
  assert.equal((await db.prepare("SELECT character FROM corpus_units WHERE id='codh:plain'").first()).character, '仮')
  await counted()
  assert.equal((await call('/atlas/corpus/character?id=codh%3Aplain')).identity_basis, 'form_cluster')
  assert.equal((await call('/atlas/corpus/character?id=codh%3Afixture')).identity_basis, 'human_review', 'a human review outranks a form')
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], form: '假', client_id: 'integration' })
  const record = await call('/atlas/corpus/character?id=codh%3Aplain')
  assert.deepEqual([record.written_character, record.identity_basis], ['假', 'form_glyph'], 'a glyph decision overrides its cluster')
  const shown = (await call('/layers/gallery')).items.find(item => item.id === 'codh:plain')
  assert.deepEqual([shown.written_character, shown.identity_basis, shown.form_cluster], ['假', 'form_glyph', { id: 'U+4EEE:c1' }], 'the gallery shows the form decision')
  const members = await call('/atlas/forms/clusters/U%2B4EEE%3Ac1')
  assert.deepEqual(members.items.map(m => [m.id, m.form, m.basis]), [['codh:plain', '假', 'form_glyph'], ['codh:fixture', '仮', 'form_cluster']])
  await call('/atlas/forms/decisions', { kind: 'inherit', units: ['codh:plain'], client_id: 'integration' })
  assert.equal((await call('/atlas/corpus/character?id=codh%3Aplain')).written_character, '仮', 'following the cluster again')
  // A reload of the same clustering with no new decision still shows once it finishes.
  assert.equal((await call('/atlas/forms/families')).items[0].label, '仮 = 假')
  await db.batch([db.prepare("UPDATE form_families SET label='仮 = 假 = 叚' WHERE code_point='U+4EEE'"),
    db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('forms_loaded_at','\"reloaded\"')")])
  assert.equal((await call('/atlas/forms/families')).items[0].label, '仮 = 假 = 叚', 'a finished reload replaces the cached families')
  await counted()
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: null, client_id: 'integration' })
  assert.equal((await db.prepare("SELECT character FROM corpus_units WHERE id='codh:plain'").first()).character, '假',
    'withdrawing the form restores the character the glyph had before')
  await counted()
  // A glyph reported as another character leaves the family: search, counts and the record show
  // that character, the family counts it as rejected, and following the cluster again takes it back.
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], form: '假', issue: 'character', client_id: 'integration' }, 422)
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], issue: 'crop', character: 'テ', client_id: 'integration' }, 422)
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], issue: 'character', character: 'テ', client_id: 'integration' })
  assert.deepEqual({ ...(await db.prepare("SELECT character,family FROM corpus_units WHERE id='codh:plain'").first()) },
    { character: 'テ', family: 'U+30C6' }, 'a glyph reported as テ joins テ\'s family (its own, as this catalogue lacks テ)')
  assert.equal((await call('/atlas/corpus/character?id=codh%3Aplain')).written_character, 'テ')
  const reported = await call('/atlas/forms/families/U%2B4EEE')
  assert.deepEqual([reported.rejected, reported.items[0].rejected], [1, 1])
  assert.equal((await call('/atlas/forms/families')).items[0].rejected, 1)
  assert.deepEqual((await call('/atlas/forms/clusters/U%2B4EEE%3Ac1')).items.map(m => [m.id, m.reported, m.character]),
    [['codh:plain', 'character', 'テ'], ['codh:fixture', null, null]])
  await counted()
  await call('/atlas/forms/decisions', { kind: 'inherit', units: ['codh:plain'], client_id: 'integration' })
  assert.deepEqual({ ...(await db.prepare("SELECT character,family FROM corpus_units WHERE id='codh:plain'").first()) },
    { character: '假', family: 'U+4EEE' }, 'taking the report back restores its character and family')
  assert.equal((await call('/atlas/forms/families/U%2B4EEE')).rejected, 0)
  await counted()
  // A cluster marked mixed names nothing for its glyphs; one reported whole reports every glyph that
  // follows it; clearing the cluster takes either back.
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: '仮', client_id: 'integration' })
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: '仮', issue: 'mixed', client_id: 'integration' }, 422)
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', issue: 'mixed', character: 'テ', client_id: 'integration' }, 422)
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], issue: 'mixed', client_id: 'integration' }, 422)
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', issue: 'mixed', client_id: 'integration' })
  const mixed = await call('/atlas/forms/families/U%2B4EEE')
  assert.deepEqual([mixed.items[0].form, mixed.items[0].issue, mixed.assigned, mixed.rejected], [null, 'mixed', 0, 0])
  assert.equal((await db.prepare("SELECT character FROM corpus_units WHERE id='codh:plain'").first()).character, '假', 'a mixed cluster withdraws its form')
  await counted()
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', issue: 'character', character: 'テ', client_id: 'integration' })
  assert.deepEqual({ ...(await db.prepare("SELECT character,family FROM corpus_units WHERE id='codh:plain'").first()) },
    { character: 'テ', family: 'U+30C6' }, 'a cluster reported as テ reports its glyphs')
  const wholly = await call('/atlas/forms/clusters/U%2B4EEE%3Ac1')
  assert.deepEqual([wholly.issue, wholly.items.map(m => [m.reported, m.character, m.basis])], ['character', [['character', 'テ', 'form_cluster'], ['character', 'テ', 'form_cluster']]])
  assert.deepEqual([(await call('/atlas/forms/families/U%2B4EEE')).rejected, (await call('/atlas/corpus/character?id=codh%3Aplain')).written_character], [2, 'テ'])
  await counted()
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: null, client_id: 'integration' })
  assert.deepEqual({ ...(await db.prepare("SELECT character,family FROM corpus_units WHERE id='codh:plain'").first()) },
    { character: '假', family: 'U+4EEE' }, 'clearing the cluster takes its report back')
  assert.deepEqual([(await call('/atlas/forms/families/U%2B4EEE')).rejected, (await call('/atlas/forms/families/U%2B4EEE')).items[0].issue], [0, null])
  await counted()
  const split = await call('/atlas/forms/split/U%2B4EEE%3Ac1?k=2')
  assert.deepEqual(split.groups.map(g => g.ids), [['codh:plain'], ['codh:fixture']])
  assert.equal((await mf.dispatchFetch(base + '/atlas/forms/families/%E0')).status, 404, 'a malformed escape names no family')
  await db.prepare("INSERT INTO form_loading VALUES('now')").run()
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], form: '假', client_id: 'integration' }, 503)
  await db.prepare('DELETE FROM form_loading').run()
  const log = await (await mf.dispatchFetch(base + '/atlas/forms/decisions.jsonl')).text()
  assert.equal(log.trim().split('\n').length, 10, 'every accepted decision is logged, the refused ones are not')
  assert.deepEqual(log.trim().split('\n').map(JSON.parse).filter(d => d.issue).map(d => [d.kind, d.issue, d.character]),
    [['glyph', 'character', 'テ'], ['cluster', 'mixed', undefined], ['cluster', 'character', 'テ']])
  // A repair verdict changed in place survives a review and its undo, and so does the quiz it decides.
  const vetted = { id: 'vetted', label: 'キ', reading: 'キ', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten',
    repair: { status: 'joined', quiz: true } }
  await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
    vetted.id, 'local', 'キ', 'キ', 'U+30AD', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
    JSON.stringify(vetted), JSON.stringify({ character: vetted }), '{}', '{}', null).run()
  const vettedRound = { id: crypto.randomUUID(), client_id: 'integration', label: 'キ',
    answers: [{ id: 'vetted', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'blank' }] }
  await call('/atlas/rounds', vettedRound)
  await db.prepare(`UPDATE units SET data=json_set(data,'$.repair',json('{"status":"uncertain","quiz":false}')), quiz=0 WHERE id='vetted' AND revision=1`).run()
  await call(`/atlas/rounds/${vettedRound.id}/undo`, { client_id: 'integration' })
  const vettedRow = await db.prepare("SELECT quiz, json_extract(data,'$.repair.quiz') AS dealt, json_extract(data,'$.state') AS state FROM units WHERE id='vetted'").first()
  assert.deepEqual(vettedRow, { quiz: 0, dealt: 0, state: 'pending' }, 'undo restores the review state and keeps the newer repair verdict out of the quiz')
  // A retired crop names the crop that replaced it: deleted, kept for its history, or through a chain.
  const retiredCrop = { id: 'retired-kept', label: 'ア', reading: 'ア', state: 'flagged', revision: 1, image_sha256: hash, production: 'handwritten' }
  await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
    retiredCrop.id, 'retired', 'ア', 'ア', 'U+30A2', null, 'handwritten', 'kana', 'flagged', 1, 0, 1, 1,
    JSON.stringify(retiredCrop), JSON.stringify({ character: retiredCrop }), '{}', '{}', null).run()
  await db.batch([['retired-gone', 'retired-kept'], ['retired-kept', 'two'], ['loop-a', 'loop-b'], ['loop-b', 'loop-a']]
    .map(([id, target]) => db.prepare('INSERT INTO unit_redirects VALUES(?,?)').bind(id, target)))
  for (const id of ['retired-gone', 'retired-kept']) {
    const response = await mf.dispatchFetch(`${base}/atlas/characters/${id}`)
    assert.equal(response.status, 404)
    assert.equal((await response.json()).replaced_by, 'two', `${id} names its current replacement`)
  }
  const looped = await mf.dispatchFetch(`${base}/atlas/characters/loop-a`)
  assert.equal(looped.status, 404)
  assert.equal((await looped.json()).replaced_by, undefined, 'a redirect loop is answered as missing')
  assert.ok(!(await call('/atlas?state=all&limit=96')).items.some(i => i.id === 'retired-kept'), 'a retired crop is in no listing')
  assert.ok(!(await call('/atlas?state=attention&limit=96')).items.some(i => i.id === 'retired-kept'), 'nor in Needs fixing')
  // Browse starts from a point the seed picks and wraps round, so every page run deals each crop once.
  await db.prepare('UPDATE units SET shuffle=rowid*1000').run()
  const everyLocal = (await db.prepare("SELECT id FROM units WHERE origin='local'").all()).results.map(r => r.id).sort()
  for (const seed of [0, 5500, 12345, 268435455, 268440000]) {
    const dealt = []
    for (let offset = 0, total = Infinity; offset < total;) {
      const page = await call(`/atlas?seed=${seed}&offset=${offset}&limit=7`)
      dealt.push(...page.items.map(i => i.id)); total = page.total; offset = page.next_offset
    }
    assert.deepEqual(dealt.slice().sort(), everyLocal, `seed ${seed} deals every crop once`)
  }
  const dealtFrom = async seed => (await call(`/atlas?seed=${seed}&limit=96`)).items.map(i => i.id)
  const [fromZero, later] = [await dealtFrom(0), await dealtFrom(5500)]
  assert.notEqual(fromZero[0], later[0], 'another seed starts elsewhere')
  assert.deepEqual(later.slice().sort(), fromZero.slice().sort(), 'and wraps round to the same crops')
  // A grapheme lists its family's crops and its own character's, each once.
  for (const [id, character, family] of [['fam-a', '假', 'U+4EEE'], ['fam-b', '仮', null], ['fam-c', '仮', 'U+4EEE'], ['fam-other', '何', 'U+4F55']]) {
    const d = { id, label: character, reading: character, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(id, 'local', character, character, family, null,
      'handwritten', 'kanji', 'pending', 0, 0, 1, 1, JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const inGrapheme = async query => { const found = await call('/layers/occurrences?code_point=U%2B4EEE' + query); return [found.total, found.items.map(i => i.id).filter(id => id.startsWith('fam-'))] }
  const localCount = async where => (await db.prepare(`SELECT count(*) AS n FROM units WHERE origin='local' AND ${where}`).first()).n
  const [graphemeTotal, characterTotal] = [await localCount("(family='U+4EEE' OR character='仮')"), await localCount("character='仮'")]
  assert.deepEqual(await inGrapheme('&scope=grapheme&limit=200'), [graphemeTotal, ['fam-a', 'fam-b', 'fam-c']], 'a grapheme lists its family and its character')
  assert.deepEqual(await inGrapheme('&limit=200'), [characterTotal, ['fam-b', 'fam-c']], 'a character lists its own crops')
  const ordered = (await call('/layers/occurrences?code_point=U%2B4EEE&scope=grapheme&limit=200')).items.map(i => i.id)
  assert.deepEqual(ordered, ordered.slice().sort(), 'in id order')
  assert.equal((await call(`/layers/occurrences?code_point=U%2B4EEE&scope=grapheme&limit=1&offset=${ordered.indexOf('fam-b')}`)).items[0].id, 'fam-b', 'and pages through them')
  // Search finds a crop by its character or by its reading, each once.
  for (const [id, character, reading] of [['find-both', 'とも', 'とも'], ['find-reading', '𪜈', 'とも'], ['find-neither', '𪜈', '𪜈']]) {
    const d = { id, label: character, reading, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(id, 'local', character, reading, null, null,
      'handwritten', 'kana', 'pending', 0, 0, 1, 1, JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const searched = async term => { const found = await call(`/atlas?q=${encodeURIComponent(term)}&limit=96`); return [found.total, found.items.map(i => i.id).filter(id => id.startsWith('find-')).sort()] }
  const matching = async term => (await db.prepare("SELECT count(*) AS n FROM units WHERE origin='local' AND (character=? OR reading=?)").bind(term, term).first()).n
  assert.deepEqual(await searched('とも'), [await matching('とも'), ['find-both', 'find-reading']], 'a search matches the character or the reading')
  assert.deepEqual(await searched('𪜈'), [await matching('𪜈'), ['find-neither', 'find-reading']])
  // A picked character within a script: the script still filters.
  const pickedIn = async group => (await call(`/atlas?reading=${encodeURIComponent('仮')}&group=${group}&limit=96`)).items.map(i => i.id).filter(id => id.startsWith('fam-')).sort()
  assert.deepEqual(await pickedIn('kanji'), ['fam-b', 'fam-c'], 'a picked character keeps its crops in its script')
  assert.deepEqual(await pickedIn('kana'), [], 'and has none in another')
  // A refresh rewrites `units` in place and stamps the catalogue; the cached browse counts follow.
  // The crops above were written straight into D1, as a refresh writes them.
  const stamp = stamped => db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',?)").bind(JSON.stringify(stamped))
  await stamp('first refresh').run()
  const checkedKa = async () => (await call('/atlas')).categories.find(c => c.label === '仮').checked
  const checkedBefore = await checkedKa()
  await db.batch([db.prepare("UPDATE units SET state='checked' WHERE id='fam-b' AND state<>'checked'"), stamp('second refresh')])
  assert.equal(await checkedKa(), checkedBefore + 1, 'a refresh shows in the browse counts')
  console.log('Workerd integration passed: atomic rounds, issue-only saves, retries, undo, corpus identity, search, gallery, export, seen crops, flagged order, corpus rounds, edit history, hosted forms.')
} finally {
  await mf.dispose()
}
