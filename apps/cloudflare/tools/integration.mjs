// Exercise the production Worker in workerd with real D1 transactions and R2 records.
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { Miniflare, convertV4MiniflareOptions } from 'miniflare'

const mf = new Miniflare(convertV4MiniflareOptions({workers:[{
  name: 'atlas-test',
  modules: true, script: await readFile('/tmp/atlas-worker-test.mjs', 'utf8'), compatibilityDate: '2026-09-22',
  d1Databases: ['DB'], r2Buckets: ['MEDIA'],
  serviceBindings: { ASSETS: () => new Response('assets') },
}]}))
try {
  const db = await mf.getD1Database('DB')
  // Every migration, in order, the way a new deployment applies them. Rows published and reviewed
  // before 0006 are written first, to show what it makes of them.
  const migrations = (await readdir(new URL('../migrations/', import.meta.url))).filter(name => name.endsWith('.sql')).sort()
  const apply = async name => {
    const schema = await readFile(new URL(`../migrations/${name}`, import.meta.url), 'utf8')
    const statements = schema.match(/CREATE TRIGGER[\s\S]*?\nEND;|(?:CREATE (?:TABLE|(?:UNIQUE )?INDEX)|DROP TRIGGER|UPDATE|ALTER TABLE|DELETE FROM|INSERT INTO) [\s\S]*?;/g)
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
  for (const name of migrations.filter(name => name >= '0006')) await apply(name)
  assert.deepEqual((await db.prepare("SELECT id,production,named FROM corpus_units WHERE character='ト' ORDER BY id").all()).results, [
    { id: 'codh-omt:1', production: 'movable-type', named: 0 },
    { id: 'codh-omtz:1', production: 'unknown', named: 0 },
    { id: 'codh:legacy', production: 'unknown', named: 1 }], 'the old movable-type set is marked, and a reviewed glyph is named')
  assert.deepEqual((await db.prepare("SELECT * FROM corpus_characters WHERE character='ト'").all()).results, [
    { character: 'ト', production: 'movable-type', n: 1, named: 0 }, { character: 'ト', production: 'unknown', n: 2, named: 1 }])
  assert.deepEqual(await db.prepare("SELECT quiz,category,shuffle FROM units WHERE id='codh:legacy'").first(),
    { quiz: 1, category: 'kana', shuffle: 79 }, 'a reviewed corpus row gets the quiz, category and shuffle the Worker gives one')
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
  await db.prepare('INSERT INTO corpus_units VALUES(?,?,?,?,?,?,?,?,?,?)').bind(corpus.id, null, 'U+4EEE', 'group-one', 1, 'fixture', 0, new TextEncoder().encode(raw).length, 'unknown', 0).run()
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
  await db.prepare('INSERT INTO unit_shapes VALUES(?,?)').bind('two', 7).run()
  const shaped = Object.fromEntries((await call('/atlas?purpose=review&production=all')).items.map(i => [i.id, i.shape_order]))
  assert.deepEqual(shaped, { one: null, two: 7 }, 'a crop carries its shape order, or null without one')
  // Flagged order: a crop already reviewed in the character inspector queues behind one nobody has.
  for (const id of ['flag-a', 'flag-b']) {
    const d = { id, label: 'ラ', reading: 'ラ', state: 'pending', revision: 0, image_sha256: hash,
      production: 'manuscript', repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'ラ', 'ラ', 'U+3042', null, 'manuscript', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}').run()
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
  await call(`/atlas/rounds/${inspected.id}/undo`, { client_id: 'inspector' })
  assert.deepEqual(await flaggedOrder(), ['flag-a', 'flag-b'], 'undoing the inspector review restores the order')
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
      image: `/atlas/media/${id}.webp`, production: 'manuscript', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
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
  await call(`/atlas/rounds/${passed.id}/undo`, { client_id: 'integration' })
  assert.deepEqual(await pendingSe(), ['seen-b'], 'undoing a pass returns its crops to the queue')
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
      production: 'manuscript', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'ソ', 'ソ', 'U+30BD', null, 'manuscript', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}').run()
  }
  const dealtTo = async reviewer => (await call(`/atlas?purpose=review&reading=ソ&state=pending&seed=3&limit=96&reviewer=${reviewer}`)).items.map(i => i.id)
  const skipBy = (reviewer, id) => ({ id: crypto.randomUUID(), client_id: reviewer, label: 'ソ', skipped: [{ id, image_sha256: hash }] })
  await call('/atlas/rounds', skipBy('alice', 'skip-b'))
  assert.ok(!(await dealtTo('alice')).includes('skip-b'), 'a skip rests for the reviewer who made it')
  assert.equal((await dealtTo('bob'))[0], 'skip-b', 'another reviewer is dealt a skipped crop first')
  assert.equal((await call('/atlas/characters/skip-b')).state, 'pending', 'a skip changes nothing about the crop')
  const second = skipBy('bob', 'skip-b')
  await call('/atlas/rounds', second)
  assert.deepEqual((await call('/atlas?state=hard&reading=ソ')).items.map(i => i.id), ['skip-b'], 'two skips make a crop hard')
  assert.ok(!(await dealtTo('carol')).includes('skip-b'), 'a hard crop leaves the rounds')
  await call(`/atlas/rounds/${second.id}/undo`, { client_id: 'bob' })
  assert.deepEqual((await call('/atlas?state=hard&reading=ソ')).items, [], 'an undo takes a skip back')
  // Corpus glyphs in Quick review: a character's local crops first, then its assigned, proxyable
  // corpus glyphs, until a round names them and they become `units` rows like any other crop.
  const worker = await import('/tmp/atlas-worker-test.mjs')
  const glyph = (id, fields = {}) => ({ id, origin: 'corpus', label: 'ナ', char: 'ナ', written_character: 'ナ',
    identity_status: 'assigned', source_label: 'ナ', reading: 'ナ', grapheme: 'U+30CA', state: 'pending', revision: 0,
    proxyable: true, production: 'woodblock', image: `/atlas/media/${id}.webp`, box: { x: 1, y: 2, w: 3, h: 4 },
    source: { corpus: 'codh-full', title: 'A woodblock book' }, source_revision: createHash('sha256').update(id).digest('hex'), ...fields })
  // shuffle 50,10,40,20,30: from seed 0 the order is na-2, na-4, na-5, na-3, na-1.
  const glyphs = [['na-1', 50], ['na-2', 10], ['na-3', 40], ['na-4', 20], ['na-5', 30]].map(([id, shuffle]) => [glyph(id), shuffle, 'ナ'])
  glyphs.push([glyph('na-movable', { production: 'movable-type' }), 15, 'ナ'],
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
      production: 'manuscript', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').bind(
      id, 'local', 'ナ', 'ナ', 'U+30CA', null, 'manuscript', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}').run()
  }
  const roundOf = async (params = '') => call(`/atlas?purpose=review&reading=ナ&state=pending${params.includes('limit=') ? '' : '&limit=96'}${params}`)
  const ids = async params => (await roundOf(params)).items.map(i => i.id)
  const dealtNa = await ids('&seed=0')
  assert.deepEqual(dealtNa.slice(0, 2).sort(), ['na-local-a', 'na-local-b'], 'local crops come first')
  assert.deepEqual(dealtNa.slice(2), ['na-2', 'na-4', 'na-5', 'na-3', 'na-1'], 'then corpus glyphs in shuffle order')
  assert.deepEqual((await ids('&seed=25')).slice(2), ['na-5', 'na-3', 'na-1', 'na-2', 'na-4'], 'the seed picks where the shuffle starts')
  const paged = []
  for (let offset = 0; offset < 7; offset += 3) paged.push(...await ids(`&seed=0&limit=3&offset=${offset}`))
  assert.deepEqual(paged, dealtNa, 'paging runs on from the local crops into the corpus glyphs')
  assert.equal((await roundOf('&seed=0')).total, 7)
  await call('/atlas?purpose=review&reading=ナ&offset=5000', undefined, 404)
  assert.ok(!dealtNa.includes('na-movable') && !dealtNa.includes('na-unassigned'), 'movable type and unassigned glyphs are not dealt')
  assert.ok((await ids('&seed=0&production=all')).includes('na-movable'), 'every material includes movable type')
  assert.deepEqual((await ids('&seed=0&production=woodblock')), ['na-2', 'na-4', 'na-5', 'na-3', 'na-1'], 'one material deals only its glyphs')
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
  assert.deepEqual(await category(), { label: 'ナ', total: 7, pending: 7, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 }, 'counts include corpus glyphs')
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
  assert.deepEqual(await category('&reviewer=alice'), { label: 'ナ', total: 4, pending: 4, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 })
  assert.deepEqual((await db.prepare("SELECT id FROM corpus_units WHERE character='ナ' AND named=1 ORDER BY id").all()).results.map(r => r.id),
    ['na-2', 'na-4', 'na-5'], 'naming a glyph marks its published row')
  assert.deepEqual(await db.prepare("SELECT n,named FROM corpus_characters WHERE character='ナ' AND production='woodblock'").first(), { n: 5, named: 3 })
  assert.equal((await call('/atlas/rounds', cropRound)).id, cropSaved.id)
  assert.equal((await db.prepare("SELECT named FROM corpus_characters WHERE character='ナ' AND production='woodblock'").first()).named, 3, 'a retried round names nothing twice')
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
    if (index) assert.ok(details.some(d => d.includes(`USING INDEX ${index} `) || d.includes(`USING COVERING INDEX ${index} `)), `${index}: ${details.join('; ')}`)
  }
  const shapes = []
  for (const production of [null, 'woodblock'])
    for (const side of ['>=', '<'])
      shapes.push([{ sql: worker.corpusRoundQuery(production, side), values: [] }, ['ナ', ...(production ? [production] : []), 0, 96],
        production ? 'corpus_material' : 'corpus_round'])
  // corpus_characters is small and read whole, in its key's order.
  for (const production of ['all', 'non-movable-type', 'woodblock']) shapes.push([worker.corpusCountQuery(production), [], null])
  shapes.push([{ sql: worker.namedRoundQuery("production!='movable-type'", 'state'), values: [] }, ['ナ'], 'unit_character'])
  // What a publication runs after it rewrites corpus_units, and what the trigger runs on each naming.
  shapes.push([{ sql: refresh[0], values: [] }, [], 'sqlite_autoindex_corpus_units_1'])
  shapes.push([{ sql: "UPDATE corpus_characters SET named=named+1 WHERE (character,production)=(SELECT character,production FROM corpus_units WHERE id=? AND named=0)", values: [] },
    ['na-1'], 'sqlite_autoindex_corpus_units_1'])
  // A document's characters are read along the table's own key, and each unit by its id.
  const documentPlan = await plan({ sql: worker.documentCharactersQuery(), values: [] }, ['hk:doc'])
  served(documentPlan, null)
  assert.ok(documentPlan.includes('SEARCH c USING PRIMARY KEY (document=?)'), documentPlan.join('; '))
  assert.ok(documentPlan.some(d => /^SEARCH u USING INDEX sqlite_autoindex_units_1 \(id=\?\)/.test(d)), documentPlan.join('; '))
  for (const [shape, bound, index] of shapes) {
    const details = await plan(shape, bound)
    if (process.env.SHOW_PLANS) console.log(index, JSON.stringify(details))
    served(details, index)
  }
  const keys = {
    corpus_round: 'CREATE INDEX corpus_round ON corpus_units(character,named,shuffle)',
    corpus_material: 'CREATE INDEX corpus_material ON corpus_units(character,production,named,shuffle)',
    unit_character: 'CREATE INDEX unit_character ON units(origin,character,state)',
  }
  for (const [index, create] of Object.entries(keys)) {
    await db.prepare(`DROP INDEX ${index}`).run()
    for (const [shape, bound] of shapes.filter(s => s[2] === index))
      await assert.rejects(async () => served(await plan(shape, bound), index), `the check on ${index} fails without it`)
    await db.prepare(create).run()
  }
  // The migration names the label categories the Worker computes, code point by code point.
  const ranges = kind => [...migration.split(`THEN '${kind}'`)[0].split('WHEN').at(-1).matchAll(/c BETWEEN (0x[0-9A-F]+) AND (0x[0-9A-F]+)|c=(0x[0-9A-F]+)/g)]
    .map(m => m[3] ? [Number(m[3]), Number(m[3])] : [Number(m[1]), Number(m[2])])
  const kana = ranges('kana'), han = ranges('kanji'), within = (list, c) => list.some(([a, b]) => a <= c && c <= b)
  for (let c = 0; c <= 0x10FFFF; c++) {
    if (c >= 0xD800 && c <= 0xDFFF) continue
    const sql = within(kana, c) ? 'kana' : within(han, c) ? 'kanji' : 'other'
    if (worker.categoryOf(String.fromCodePoint(c)) !== sql) assert.fail(`U+${c.toString(16)}: ${worker.categoryOf(String.fromCodePoint(c))} vs ${sql}`)
  }
  console.log('Workerd integration passed: atomic rounds, issue-only saves, retries, undo, corpus identity, search, gallery, export, seen crops, flagged order, corpus rounds.')
} finally {
  await mf.dispose()
}
