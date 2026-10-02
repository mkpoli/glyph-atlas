// Exercise the production Worker in workerd with real D1 transactions and R2 records.
// Run from apps/cloudflare: `bun tools/integration.mjs`. It bundles this checkout's src/index.ts to a
// temporary directory of its own, so the Worker under test is always the one beside it.
import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'
import { Miniflare, convertV4MiniflareOptions } from 'miniflare'

const bundleDir = await mkdtemp(join(tmpdir(), 'atlas-worker-'))
const bundle = join(bundleDir, 'worker.mjs')
await build({ entryPoints: [fileURLToPath(new URL('../src/index.ts', import.meta.url))], outfile: bundle, bundle: true,
  format: 'esm', platform: 'neutral', conditions: ['workerd', 'worker', 'browser'], mainFields: ['module', 'main'],
  external: ['node:*'], logLevel: 'error' })
const mf = new Miniflare(convertV4MiniflareOptions({workers:[{
  name: 'atlas-test',
  modules: true, script: await readFile(bundle, 'utf8'), compatibilityDate: '2026-09-22', compatibilityFlags: ['nodejs_compat'],
  d1Databases: ['DB'], r2Buckets: ['MEDIA'], bindings: { BETTER_AUTH_SECRET: 'integration-test-secret-integration-test' },
  ratelimits: { CORRECTIONS: { namespace_id: '4401', simple: { limit: 20, period: 60 } },
    WRITTEN_FORMS: { namespace_id: '4402', simple: { limit: 30, period: 60 } } },
}]}))
try {
  const db = await mf.getD1Database('DB')
  // The columns a fixture row fills; later ones (`style`) take their defaults.
  const UNIT_COLUMNS = 'id,origin,character,reading,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document'
  const CORPUS_COLUMNS = 'id,character,family,visual_group,shuffle,object,offset,size,production,named'
  // Every migration, in order, the way a new deployment applies them. Rows published and reviewed
  // before 0006 are written first, to show what it makes of them.
  const migrations = (await readdir(new URL('../migrations/', import.meta.url))).filter(name => name.endsWith('.sql')).sort()
  const apply = async name => {
    const schema = await readFile(new URL(`../migrations/${name}`, import.meta.url), 'utf8')
    // Comments go first: a comment line that starts with a keyword would otherwise read as a statement.
    const statements = schema.replace(/^\s*--.*$/gm, '').match(/CREATE TRIGGER[\s\S]*?\nEND;|(?:CREATE (?:TABLE|(?:UNIQUE )?INDEX)|DROP (?:TRIGGER|INDEX)|UPDATE|ALTER TABLE|DELETE FROM|INSERT INTO) [\s\S]*?;/g)
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
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
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
  await db.prepare(`INSERT INTO corpus_units(${CORPUS_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?)`).bind(corpus.id, null, 'U+4EEE', 'group-one', 1, 'fixture', 0, new TextEncoder().encode(raw).length, 'unknown', 0).run()
  // The gallery deals copies of the published records; one copied from a pack a later publication
  // replaced no longer matches its row, and is not dealt.
  const stale = { ...corpus, id: 'codh:stale', label: '古' }
  await db.batch([db.prepare('INSERT INTO corpus_gallery VALUES(?,?,?,?,?)').bind(corpus.id, 1, 'fixture', 0, raw),
    db.prepare(`INSERT INTO corpus_units(${CORPUS_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?)`).bind(stale.id, null, 'U+53E4', null, 2, 'newer-pack', 0, 10, 'unknown', 0),
    db.prepare('INSERT INTO corpus_gallery VALUES(?,?,?,?,?)').bind(stale.id, 2, 'older-pack', 0, JSON.stringify(stale))])
  // Miniflare hands the Worker its own loopback address, so the page origin a browser would send is
  // that address; a fixed `http://localhost` fails the Worker's same-origin check on every POST.
  const base = new URL(await mf.ready).origin
  // Each reviewer signs in as a browser does before its first save, through the Worker's own anonymous
  // sign-in, and every request made as that reviewer carries the session cookie it set.
  const users = {}
  async function signIn(name) {
    const response = await mf.dispatchFetch(base + '/api/auth/sign-in/anonymous', {
      method: 'POST', headers: { 'content-type': 'application/json', origin: base }, body: '{}' })
    const json = await response.json()
    assert.equal(response.status, 200, 'sign-in ' + JSON.stringify(json))
    return users[name] = { id: json.user.id, cookie: response.headers.getSetCookie().map(c => c.split(';')[0]).join('; ') }
  }
  const user = async name => users[name] ?? signIn(name)
  // A POST is made as `integration` and a GET anonymously, unless a reviewer is named.
  async function call(path, value, status = 200, as = value ? 'integration' : null) {
    const cookie = as ? { cookie: (await user(as)).cookie } : {}
    const response = await mf.dispatchFetch(base + path, value ? {
      method: 'POST', headers: { 'content-type': 'application/json', origin: base, ...cookie }, body: JSON.stringify(value),
    } : { headers: cookie })
    const json = await response.json()
    assert.equal(response.status, status, path + ' ' + JSON.stringify(json))
    return json
  }
  const decision = { id: 'one', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'merged', correction: 'アイ' }
  const round = { id: crypto.randomUUID(), label: 'ア', answers: [decision] }
  assert.deepEqual(await call('/atlas/rounds', round, 401, null), { detail: 'Sign in to save.' }, 'an anonymous save is refused')
  assert.equal((await call('/atlas/characters/one')).revision, 0, 'and changes nothing')
  const saved = await call('/atlas/rounds', round)
  assert.equal((await call('/api/account', undefined, 200, 'integration')).user.anonymous, true, 'the browser holds an anonymous session')
  assert.deepEqual(await call('/atlas/rounds', round), saved)
  assert.equal((await call('/atlas/characters/two')).state, 'pending', 'unselected is unjudged')
  assert.equal((await call('/atlas/characters/one')).revision, 1)
  await call('/atlas/rounds', { ...round, id: crypto.randomUUID(), answers: [
    { ...decision, id: 'two' }, { ...decision, revision: 0 },
  ] }, 409)
  assert.equal((await call('/atlas/characters/two')).revision, 0, 'stale rounds are atomic')
  await call(`/atlas/rounds/${round.id}/undo`, {})
  const restored = await call('/atlas/characters/one')
  assert.equal(restored.state, 'pending')
  assert.equal(restored.revision, 2)
  const unassigned = '/layers/candidates?code_point=U%2B4EEE&scope=grapheme&visual_group=unassigned'
  assert.equal((await call(unassigned)).glyphs, 1, 'unlabeled visual groups are unassigned')
  const correction = { id: crypto.randomUUID(), identity: corpus.id,
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
  const cropProblem = { id: crypto.randomUUID(), revision: 2,
    image_sha256: hash, verdict: 'wrong', issue: 'crop', character: '仮' }
  await call('/atlas/characters/one', cropProblem)
  assert.equal((await call('/atlas/characters/one')).state, 'flagged', 'text edits do not resolve bad geometry')
  assert.equal((await call('/atlas?group=kanji')).items[0].id, 'one', 'category follows the written identity')
  // A publication corrects the page number in place; undoing a review made before that keeps it.
  await db.prepare("UPDATE units SET data=json_set(data,'$.page_number',41) WHERE id='one'").run()
  await call(`/atlas/rounds/${cropProblem.id}/undo`, {})
  assert.equal((await call('/atlas/characters/one')).page_number, 41, 'an undo keeps the corrected page number')
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
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ラ', 'ラ', 'U+3042', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const flagRound = { id: crypto.randomUUID(), label: 'ラ', answers: [
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
  const inspected = { id: crypto.randomUUID(), revision: 1,
    image_sha256: hash, verdict: 'wrong', issue: 'crop' }
  await call('/atlas/characters/flag-a', inspected, 200, 'inspector')
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
  await call(`/atlas/rounds/${inspected.id}/undo`, {}, 200, 'inspector')
  assert.deepEqual(await flaggedOrder(), ['flag-a', 'flag-b'], 'undoing the inspector review restores the order')
  assert.equal((await flagged('hide')).reported_count, 0, 'undoing the review brings the crop back')
  assert.deepEqual((await flagged('hide')).items.map(i => i.id).sort(), ['flag-a', 'flag-b'])
  const ligature = { char: '𪜈', code_point: 'U+2A708', grapheme: { code_point: 'U+2A708' }, ligature: { reading: 'トモ' }, candidates: {} }
  await db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind('U+2A708', '𪜈', '', JSON.stringify(ligature), JSON.stringify(ligature)).run()
  const reading = { id: crypto.randomUUID(), revision: 0,
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
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'セ', 'セ', 'U+30BB', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  // Browse counts are cached per version of the data, so each write below must show in them at once.
  const browseSe = async () => { const { pending, seen, flagged } = (await call('/atlas')).categories.find(c => c.label === 'セ'); return { pending, seen, flagged } }
  assert.deepEqual(await browseSe(), { pending: 3, seen: 0, flagged: 0 })
  const pendingSe = async () => (await call('/atlas?purpose=review&reading=セ&state=pending&limit=96')).items.map(i => i.id).sort()
  const passed = { id: crypto.randomUUID(), label: 'セ',
    seen: [{ id: 'seen-a', image_sha256: hash }, { id: 'seen-b', image_sha256: hash }, { id: 'seen-c', image_sha256: 'c'.repeat(64) }] }
  const recorded = await call('/atlas/rounds', passed)
  assert.equal(recorded.results.filter(r => r.field === 'seen').length, 2, 'a crop whose pixels changed is skipped')
  assert.deepEqual(await call('/atlas/rounds', passed), recorded, 'a retried pass is the same pass')
  const scrolled = { ...passed, seen: [...passed.seen, { id: 'seen-extra', image_sha256: hash }] }
  assert.deepEqual(await call('/atlas/rounds', scrolled), recorded, 'a retry with more crops on screen returns the first result')
  await call('/atlas/rounds', { id: crypto.randomUUID(), seen: [{ id: 'seen-a', image_sha256: hash }] }, 422)
  assert.deepEqual(await pendingSe(), ['seen-c'], 'seen crops leave the queue')
  assert.deepEqual(await browseSe(), { pending: 1, seen: 2, flagged: 0 }, 'a round shows in the browse counts')
  const summary = await call('/atlas?purpose=review&reading=セ')
  assert.equal(summary.counts.seen, 2)
  assert.equal(summary.items.find(i => i.id === 'seen-a').state, 'seen')
  assert.equal((await call('/atlas/characters/seen-a')).revision, 0, 'seeing a crop changes nothing about it')
  assert.ok(!(await call('/atlas/reviews.json?include_processed=true')).reviews.some(r => r.event?.target_id?.startsWith('seen-')), 'seen is not a review')
  const flagOnSeen = { id: crypto.randomUUID(), label: 'セ',
    answers: [{ id: 'seen-a', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'crop' }], seen: [{ id: 'seen-c', image_sha256: hash }] }
  await call('/atlas/rounds', flagOnSeen, 200, 'second')
  assert.equal((await call('/atlas/characters/seen-a')).state, 'flagged', 'a seen crop can still be flagged at its revision')
  assert.deepEqual(await pendingSe(), [], 'answers and seen crops save together')
  assert.deepEqual(await browseSe(), { pending: 0, seen: 2, flagged: 1 })
  await call(`/atlas/rounds/${passed.id}/undo`, {})
  assert.deepEqual(await pendingSe(), ['seen-b'], 'undoing a pass returns its crops to the queue')
  assert.deepEqual(await browseSe(), { pending: 1, seen: 1, flagged: 1 }, 'undoing a round with no reviews shows in the browse counts')
  await call('/atlas/rounds', { id: crypto.randomUUID(), label: 'セ', answers: [], seen: [] }, 422)
  // A crop re-cut after the round was dealt shows another image; the reader never saw that one.
  const recut = await call('/atlas/rounds', { id: crypto.randomUUID(), label: 'セ',
    seen: [{ id: 'seen-b', image_sha256: hash, image: '/atlas/media/an-older-cut.webp' }] })
  assert.equal(recut.results.filter(r => r.field === 'seen').length, 0, 'a crop re-cut since the round was dealt is not seen')
  const dealt = await call('/atlas/rounds', { id: crypto.randomUUID(), label: 'セ',
    seen: [{ id: 'seen-b', image_sha256: hash, image: '/atlas/media/seen-b.webp' }] })
  assert.equal(dealt.results.filter(r => r.field === 'seen').length, 1, 'the crop the round showed is seen')
  // Skipped crops: recorded against the reviewer, dealt first to others, rested for the one who
  // skipped, hard once two reviewers skipped them, and taken back by an undo.
  for (const id of ['skip-a', 'skip-b', 'skip-c']) {
    const d = { id, label: 'ソ', reading: 'ソ', state: 'pending', revision: 0, image_sha256: hash,
      production: 'handwritten', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ソ', 'ソ', 'U+30BD', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const dealtTo = async reviewer => (await call('/atlas?purpose=review&reading=ソ&state=pending&seed=3&limit=96', undefined, 200, reviewer)).items.map(i => i.id)
  const skipBy = id => ({ id: crypto.randomUUID(), label: 'ソ', skipped: [{ id, image_sha256: hash }] })
  await call('/atlas/rounds', skipBy('skip-b'), 200, 'alice')
  assert.ok(!(await dealtTo('alice')).includes('skip-b'), 'a skip rests for the reviewer who made it')
  // A reviewer's own skips show only in the counts of their own rounds, whichever request comes first;
  // browse counts are the same for every visitor.
  const skippedSo = async (path, as = null) => (await call(path, undefined, 200, as)).categories.find(c => c.label === 'ソ').skipped
  assert.equal(await skippedSo('/atlas?purpose=review&limit=1'), 0)
  assert.equal(await skippedSo('/atlas?purpose=review&limit=1', 'alice'), 1, 'a reviewer sees their own skip')
  assert.equal(await skippedSo('/atlas?purpose=review&limit=1'), 0, 'nobody else sees it')
  assert.equal(await skippedSo('/atlas', 'alice'), 0, 'nor do the browse counts, even for the reviewer')
  assert.equal((await dealtTo('bob'))[0], 'skip-b', 'another reviewer is dealt a skipped crop first')
  assert.equal((await call('/atlas/characters/skip-b')).state, 'pending', 'a skip changes nothing about the crop')
  const second = skipBy('skip-b')
  await call('/atlas/rounds', second, 200, 'bob')
  assert.deepEqual((await call('/atlas?state=hard&reading=ソ')).items.map(i => i.id), ['skip-b'], 'two skips make a crop hard')
  assert.ok((await call('/atlas?state=attention&reading=ソ')).items.some(i => i.id === 'skip-b'), 'the Flagged view lists hard crops')
  assert.ok(!(await dealtTo('carol')).includes('skip-b'), 'a hard crop leaves the rounds')
  // A crop is hard at the box it was skipped at, whoever moves the box: a refresh updating it in place,
  // or a publication writing the crop anew.
  const hardSo = async () => (await call('/atlas?state=hard&reading=ソ')).items.map(i => i.id)
  const moveBox = box => db.prepare("UPDATE units SET data=json_set(data,'$.box',json(?)) WHERE id='skip-b'").bind(box).run()
  await moveBox('{"x":9,"y":2,"w":3,"h":4}')
  assert.deepEqual(await hardSo(), [], 'a crop moved to another box is not hard there')
  await moveBox('{"x":1,"y":2,"w":3,"h":4}')
  assert.deepEqual(await hardSo(), ['skip-b'], 'and is again once it is back')
  // A generated column (`style_order`) takes no value.
  const { style_order: _order, ...skipB } = await db.prepare("SELECT * FROM units WHERE id='skip-b'").first()
  const rewrite = data => db.prepare(`INSERT OR REPLACE INTO units VALUES(${Object.keys(skipB).map(() => '?').join(',')})`).bind(...Object.values({ ...skipB, data })).run()
  await rewrite(JSON.stringify({ ...JSON.parse(skipB.data), box: { x: 7, y: 2, w: 3, h: 4 } }))
  assert.deepEqual(await hardSo(), [], 'nor is a crop written anew at another box')
  await rewrite(skipB.data)
  assert.deepEqual(await hardSo(), ['skip-b'])
  await call(`/atlas/rounds/${second.id}/undo`, {}, 200, 'bob')
  assert.deepEqual((await call('/atlas?state=hard&reading=ソ')).items, [], 'an undo takes a skip back')
  // Corpus glyphs in Quick review: a character's local crops first, then its assigned, proxyable
  // corpus glyphs, until a round names them and they become `units` rows like any other crop.
  const worker = await import(bundle)
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
    await db.prepare(`INSERT INTO corpus_units(${CORPUS_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?)`).bind(record.id, character, 'U+30CA', null, shuffle,
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
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ナ', 'ナ', 'U+30CA', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const roundOf = async (params = '', as = null) => call(`/atlas?purpose=review&reading=ナ&state=pending${params.includes('limit=') ? '' : '&limit=96'}${params}`, undefined, 200, as)
  const ids = async (params, as) => (await roundOf(params, as)).items.map(i => i.id)
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
  const category = async (as = null) => (await call('/atlas?purpose=review&limit=1', undefined, 200, as)).categories.find(c => c.label === 'ナ')
  assert.deepEqual(await category(), { label: 'ナ', grapheme: 'U+30CA', total: 7, pending: 7, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 }, 'counts include corpus glyphs')
  assert.equal((await call('/atlas?purpose=review&limit=1&production=all')).categories.find(c => c.label === 'ナ').pending, 8)
  const na = Object.fromEntries((await roundOf('&seed=0')).items.map(i => [i.id, i]))
  const cropRound = { id: crypto.randomUUID(), label: 'ナ',
    answers: [{ id: 'na-2', revision: 0, source_revision: na['na-2'].source_revision, verdict: 'wrong', issue: 'crop' }],
    seen: [{ id: 'na-4', source_revision: na['na-4'].source_revision, image: na['na-4'].image }],
    skipped: [{ id: 'na-5', source_revision: na['na-5'].source_revision, image: na['na-5'].image }] }
  const cropSaved = await call('/atlas/rounds', cropRound, 200, 'alice')
  assert.deepEqual(cropSaved.results.map(r => r.field), ['review', 'seen', 'skip'])
  assert.deepEqual(await call('/atlas/rounds', cropRound, 200, 'alice'), cropSaved, 'a retried corpus round is the same round')
  const aliceIds = await ids('&seed=0', 'alice')
  assert.deepEqual(aliceIds.slice(2), ['na-3', 'na-1'], 'flagged, seen and skipped glyphs leave the next round')
  assert.equal((await ids('&seed=0', 'bob'))[0], 'na-5', 'another reviewer is dealt a skipped corpus glyph first')
  // Counts cover local crops and untouched glyphs; named corpus glyphs are no longer counted anywhere.
  assert.deepEqual(await category('alice'), { label: 'ナ', grapheme: 'U+30CA', total: 4, pending: 4, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 })
  assert.deepEqual((await db.prepare("SELECT id FROM corpus_units WHERE character='ナ' AND named=1 ORDER BY id").all()).results.map(r => r.id),
    ['na-2', 'na-4', 'na-5'], 'naming a glyph marks its published row')
  assert.deepEqual(await db.prepare("SELECT n,named FROM corpus_characters WHERE character='ナ' AND production='printed/woodblock'").first(), { n: 5, named: 3 })
  assert.equal((await call('/atlas/rounds', cropRound, 200, 'alice')).id, cropSaved.id)
  assert.equal((await db.prepare("SELECT named FROM corpus_characters WHERE character='ナ' AND production='printed/woodblock'").first()).named, 3, 'a retried round names nothing twice')
  const materialised = await db.prepare("SELECT id,origin,quiz,state,shuffle FROM units WHERE id LIKE 'na-%' AND origin='corpus' ORDER BY id").all()
  assert.deepEqual(materialised.results, [
    { id: 'na-2', origin: 'corpus', quiz: 1, state: 'flagged', shuffle: 10 },
    { id: 'na-4', origin: 'corpus', quiz: 1, state: 'pending', shuffle: 20 },
    { id: 'na-5', origin: 'corpus', quiz: 1, state: 'pending', shuffle: 30 }], 'a round writes the units rows it names')
  assert.equal((await call('/atlas/corpus/character?id=na-2')).state, 'flagged')
  await call(`/atlas/rounds/${cropRound.id}/undo`, {}, 200, 'alice')
  const undone = await ids('&seed=0', 'alice')
  assert.deepEqual(undone.slice(0, 2).sort(), ['na-local-a', 'na-local-b'], 'local crops still come first')
  assert.deepEqual(undone.slice(2).sort(), ['na-1', 'na-2', 'na-3', 'na-4', 'na-5'], 'undo returns the glyphs to the round')
  assert.equal((await roundOf('&seed=0', 'alice')).total, 7, 'the round of their own character counts them again')
  // Should a named glyph's published row read as untouched, it is still dealt once.
  await db.prepare("UPDATE corpus_units SET named=0 WHERE id='na-2'").run()
  const once = await ids('&seed=0', 'alice')
  assert.equal(once.length, new Set(once).size, 'a round never returns the same crop twice')
  assert.equal(once.filter(id => id === 'na-2').length, 1)
  await db.prepare("UPDATE corpus_units SET named=1 WHERE id='na-2'").run()
  assert.equal((await db.prepare("SELECT quiz FROM units WHERE id='na-2'").first()).quiz, 1, 'undo keeps a dealable glyph in the quiz')
  // A glyph the site may not serve cannot be named by a round, nor answered in one.
  const refused = await call('/atlas/rounds', { id: crypto.randomUUID(), label: 'ヌ',
    seen: [{ id: 'nu-private', source_revision: createHash('sha256').update('nu-private').digest('hex') }] }, 200, 'alice')
  assert.equal(refused.results.length, 0)
  await call('/atlas/rounds', { id: crypto.randomUUID(), label: 'ナ', answers: [{ id: 'na-unassigned', revision: 0,
    source_revision: createHash('sha256').update('na-unassigned').digest('hex'), verdict: 'wrong', issue: 'crop' }] }, 409, 'alice')
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
  // One character's crops, by either index that starts with it: the planner may take either.
  const ONE_CHARACTER = 'unit_character(?:_style)?'
  for (const production of [null, 'printed/woodblock'])
    for (const side of ['>=', '<'])
      shapes.push([{ sql: worker.corpusRoundQuery(production, side), values: [] }, ['ナ', ...(production ? [production] : []), 0, 96],
        production ? 'corpus_material' : 'corpus_round'])
  // corpus_characters is small and read whole, in its key's order.
  for (const production of ['all', 'not:printed/type', 'printed/woodblock']) shapes.push([worker.corpusCountQuery(production), [], null])
  shapes.push([{ sql: worker.namedRoundQuery('NOT (production>=? AND production<?)', 'state'), values: [] },
    ['ナ', 'printed/type', 'printed/type0'], ONE_CHARACTER])
  // What a publication runs after it rewrites corpus_units, and what the trigger runs on each naming.
  shapes.push([{ sql: refresh[0], values: [] }, [], 'sqlite_autoindex_corpus_units_1'])
  shapes.push([{ sql: "UPDATE corpus_characters SET named=named+1 WHERE (character,production)=(SELECT character,production FROM corpus_units WHERE id=? AND named=0)", values: [] },
    ['na-1'], 'sqlite_autoindex_corpus_units_1'])
  // GET /atlas/history: all history newest first, by user, and by label, each served by its own partial index.
  shapes.push([{ sql: worker.historyQuery(null, null, null).sql, values: [] }, [41], 'event_history'])
  shapes.push([{ sql: worker.historyQuery('integration', null, null).sql, values: [] }, ['integration', 41], 'event_actor_history'])
  shapes.push([{ sql: worker.historyQuery(null, 'ア', null).sql, values: [] }, ['ア', 41], 'event_label_history'])
  // The keyset cursor stays on the same index once a page is under way, for the plain and the user shape.
  const cursor = { at: '2026-01-01T00:00:00.000Z', id: 'cf:0' }
  shapes.push([{ sql: worker.historyQuery(null, null, cursor).sql, values: [] }, [cursor.at, cursor.id, 41], 'event_history'])
  shapes.push([{ sql: worker.historyQuery('integration', null, cursor).sql, values: [] }, ['integration', cursor.at, cursor.id, 41], 'event_actor_history'])
  // Browsing one grapheme deals its crops from the seed's point in shuffle order, as `catalogue` asks.
  shapes.push([{ sql: "SELECT * FROM units WHERE origin='local' AND family=? AND shuffle>=? ORDER BY shuffle,rowid LIMIT ? OFFSET ?", values: [] },
    ['U+4EEE', 0, 60, 0], 'unit_family_sample'])
  // A round and its reference strips count their own character only, through its index; the review
  // filter off its index keeps the planner from walking every crop that can be dealt.
  const roundFilter = worker.listingFilter(true, 'not:printed/type', 'ナ')
  shapes.push([{ sql: worker.facetsQueries(true, 'integration', roundFilter.where, true).stored, values: [] }, roundFilter.values, ONE_CHARACTER])
  // Every character's counts find a reviewer's skips during the rest by who and when, read each mark
  // once and look its crop up by id.
  const allCounts = worker.facetsQueries(true, 'integration', worker.listingFilter(true, 'not:printed/type', null).where, false)
  shapes.push([{ sql: allCounts.skipped, values: [] }, ['printed/type', 'printed/type0'], 'skip_actor'])
  const markPlan = await plan({ sql: allCounts.marked, values: [] }, ['printed/type', 'printed/type0'])
  assert.ok(markPlan.some(d => /^SCAN m\b/.test(d)) && markPlan.some(d => /^SEARCH units USING INDEX sqlite_autoindex_units_1 \(id=\?\)/.test(d)), markPlan.join('; '))
  shapes.push([worker.corpusCountQuery('not:printed/type', 'ナ'), [], null])
  // Browse's corpus counts read corpus_characters whole in its key's order and look each character's
  // grapheme up by its text.
  for (const production of ['all', 'not:printed/type']) {
    const details = await plan(worker.browseCorpusQuery(production), [])
    served(details, null)
    assert.ok(details.some(d => /SEARCH ch USING (COVERING )?INDEX character_text \(character=\?\)/.test(d)), details.join('; '))
    assert.ok(!details.some(d => d.includes('TEMP B-TREE')), details.join('; '))
  }
  // The grapheme browser's corpus counts: every character with corpus glyphs, one held by no crop of the
  // collection (債) included, in the material asked for. Catalogue responses carry none of it.
  await db.batch([
    db.prepare("INSERT INTO corpus_characters VALUES('債','printed',8,0)"),
    db.prepare("INSERT OR REPLACE INTO metadata VALUES('corpus_counts_at','\"browse-corpus-test\"')"),
  ])
  const corpusItems = async (query = '') => Object.fromEntries((await call('/atlas/corpus/characters' + query)).items.map(([label, grapheme, n]) => [label, [grapheme, n]]))
  assert.deepEqual((await corpusItems())['債'], ['U+50B5', 8], 'a corpus-only character is listed with its grapheme and count')
  assert.equal((await corpusItems('?production=not:printed'))['債'], undefined, 'the count follows the material')
  await call('/atlas/corpus/characters?production=printed%20type', undefined, 400)
  assert.equal((await mf.dispatchFetch(base + '/atlas/corpus/characters')).headers.get('cache-control'), 'private, max-age=300')
  const listedCorpus = await corpusItems(), browseCategories = (await call('/atlas')).categories
  const both = browseCategories.find(c => listedCorpus[c.label])
  assert.ok(both && both.total > 0 && both.total === both.pending + both.seen + both.checked + both.flagged + both.hard,
    'a character in both lists keeps its crop counts in the catalogue')
  for (const query of ['', '?state=attention'])
    assert.ok((await call('/atlas' + query)).categories.every(c => !('corpus' in c) && c.label !== '債'), `no corpus counts in /atlas${query}`)
  // Review counts unnamed corpus glyphs as pending, as it did.
  assert.equal((await call('/atlas?purpose=review&production=all')).categories.find(c => c.label === '債')?.pending, 8)
  await db.batch([
    db.prepare("DELETE FROM corpus_characters WHERE character='債'"),
    db.prepare("INSERT OR REPLACE INTO metadata VALUES('corpus_counts_at','\"browse-corpus-test-2\"')"),
  ])
  assert.equal((await corpusItems())['債'], undefined, 'a new stamp replaces the cached counts')
  // A character card lists its 異体字 edges both ways: the variants a gallery widens to apart from the
  // rest, a pair any source calls simplified among the rest, each edge with its relation, source and
  // claims, the sources cited, and crop counts from the kept counts.
  await db.batch([
    db.prepare("INSERT INTO character_variants VALUES('仮','假','variant','wikidata','Q1; P248 Q2',1,1)"),
    db.prepare("INSERT INTO character_variants VALUES('假','仮','shinjitai','opencc','JPShinjitaiCharacters',1,1)"),
    db.prepare("INSERT INTO character_variants VALUES('反','仮','borrowed','cjkvi-variants','反→仮 hydcd/borrowed',0,0)"),
    db.prepare("INSERT INTO character_variants VALUES('仮','伋','variant','cjkvi-variants','仮→伋 hydzd/variant',1,1)"),
    db.prepare("INSERT INTO character_variants VALUES('伋','仮','simplified','unihan','伋→仮 kSimplifiedVariant',1,0)"),
    db.prepare(`INSERT OR REPLACE INTO metadata VALUES('variant_sources','{"wikidata":"Wikidata, P5475; CC0-1.0","opencc":"OpenCC; Apache-2.0","cjkvi-variants":"CJKVI; PD","unihan":"Unihan; Unicode-3.0"}')`),
    db.prepare("INSERT OR REPLACE INTO metadata VALUES('units_refreshed_at','\"variants-test\"')"),
  ])
  const card = await call('/layers/characters/U%2B4EEE')
  assert.deepEqual(card.variants.items.map(v => [v.char, v.code_point, v.sources]), [['假', 'U+5047', ['opencc', 'wikidata']]])
  assert.deepEqual(card.variants.related.map(v => [v.char, v.relations.map(r => r.relation).sort()]),
    [['伋', ['simplified', 'variant']], ['反', ['borrowed']]], 'a simplified pair is kept apart; every relation is listed')
  assert.equal(card.variants.sources.wikidata, 'Wikidata, P5475; CC0-1.0', 'each source used is cited')
  assert.equal(typeof card.variants.items[0].count, 'number')
  const edgePlan = await plan({ sql: worker.variantEdgesQuery(), values: [] }, ['仮', '仮'])
  assert.ok(edgePlan.includes('SEARCH character_variants USING PRIMARY KEY (a=?)'), edgePlan.join('; '))
  assert.ok(edgePlan.includes('SEARCH character_variants USING INDEX character_variant_b (b=?)'), edgePlan.join('; '))
  const countPlan = await plan({ sql: worker.variantCountsQuery(2), values: [] }, ['假', '反'])
  assert.ok(countPlan.some(d => /SEARCH unit_counts USING PRIMARY KEY \(origin=\? AND character=\?\)/.test(d)), countPlan.join('; '))
  assert.ok(!countPlan.some(d => /^SCAN/.test(d)), countPlan.join('; '))
  // A gallery widened to its variants deals a variant's crops with the character's own, and only then.
  const variantCrop = { id: 'variant-crop', label: '假', reading: '假', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('variant-crop', 'local', '假', '假', 'U+4EEE', null,
    'handwritten', 'kanji', 'pending', 0, 1, 1, 1, JSON.stringify(variantCrop), JSON.stringify({ character: variantCrop }), '{}', '{}', null).run()
  assert.ok(!(await call('/layers/occurrences?code_point=U%2B4EEE')).items.some(i => i.id === 'variant-crop'), 'the exact character alone')
  assert.ok((await call('/layers/occurrences?code_point=U%2B4EEE&expand=variants')).items.some(i => i.id === 'variant-crop'), 'widened to 假')
  // A crop of 伋 is not dealt: a source calls the pair a simplification, so it is kept apart.
  const apart = { ...variantCrop, id: 'apart-crop', label: '伋', reading: '伋' }
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('apart-crop', 'local', '伋', '伋', 'U+4EEE', null,
    'handwritten', 'kanji', 'pending', 0, 1, 1, 1, JSON.stringify(apart), JSON.stringify({ character: apart }), '{}', '{}', null).run()
  assert.ok(!(await call('/layers/occurrences?code_point=U%2B4EEE&expand=variants')).items.some(i => i.id === 'apart-crop'), 'a simplified pair is not widened to')
  assert.equal((await call('/layers/candidates?code_point=U%2B4EEE&scope=variants')).retry, false, 'the corpus side widens without error')
  await call('/layers/occurrences?code_point=U%2B4EEE&expand=variants&offset=2001', undefined, 404)
  // The widening reads the character's edges by key and index, and each widened page and count reads
  // the chosen characters in index order: no temporary sort, a count that stops at the cap.
  const widenPlan = await plan({ sql: worker.writtenVariantsQuery(), values: [] }, ['仮', '仮', '仮'])
  assert.ok(widenPlan.includes('SEARCH character_variants USING PRIMARY KEY (a=?)'), widenPlan.join('; '))
  assert.ok(widenPlan.includes('SEARCH character_variants USING INDEX character_variant_b (b=?)'), widenPlan.join('; '))
  const sortFree = details => {
    assert.ok(!details.some(d => /TEMP B-TREE/.test(d)), details.join('; '))
    assert.ok(!details.some(d => /^SCAN (units|corpus_units|c|u)\b/.test(d)), details.join('; '))
  }
  const cropsPlan = await plan({ sql: worker.widenedCropsQuery(3), values: [] }, ['local', '仮', '假', '反', 60, 0])
  sortFree(cropsPlan)
  assert.ok(cropsPlan.some(d => /SEARCH units USING INDEX unit_character_style \(origin=\? AND character=\?\)/.test(d)), cropsPlan.join('; '))
  const cropsCountPlan = await plan({ sql: worker.widenedCropsCountQuery(3), values: [] }, ['local', '仮', '假', '反'])
  sortFree(cropsCountPlan)
  for (const n of [1, 3]) {
    const chars = ['仮', '假', '反'].slice(0, n)
    for (const [filter, extra] of [['1=1', []], ['1=1 AND s=?', [0]]]) {
      const corpusPlan = await plan({ sql: `SELECT * FROM (${worker.corpusSelection('character', n)}) WHERE ${filter} ORDER BY k,s,i LIMIT ? OFFSET ?`, values: [] }, [...chars, ...chars, ...extra, 60, 0])
      sortFree(corpusPlan)
      assert.ok(corpusPlan.some(d => /SEARCH c USING INDEX corpus_character_style \(character=\?/.test(d)), corpusPlan.join('; '))
      assert.ok(corpusPlan.some(d => /SEARCH u USING INDEX unit_corpus_character_style/.test(d)), corpusPlan.join('; '))
    }
  }
  // A grapheme's corpus glyphs are read along the family's style index, with or without a style.
  for (const [filter, extra] of [['1=1', []], ['1=1 AND s=?', [2]]]) {
    const familyPlan = await plan({ sql: `SELECT * FROM (${worker.corpusSelection('family', 1)}) WHERE ${filter} ORDER BY k,s,i LIMIT ? OFFSET ?`, values: [] }, ['U+4EEE', 'U+4EEE', ...extra, 60, 0])
    sortFree(familyPlan)
    assert.ok(familyPlan.some(d => /SEARCH c USING INDEX corpus_family_style \(family=\?/.test(d)), familyPlan.join('; '))
  }
  // A character's and a grapheme's own crops, with or without a style, each read along a style index.
  for (const extra of ['', ' AND style_order=?']) {
    const bound = extra ? [1] : []
    const characterPlan = await plan({ sql: worker.characterCropsQuery(extra), values: [] }, ['local', '仮', ...bound, 60, 0])
    sortFree(characterPlan)
    assert.ok(characterPlan.some(d => /SEARCH units USING INDEX unit_character_style \(origin=\? AND character=\?/.test(d)), characterPlan.join('; '))
    const graphemePlan = await plan({ sql: worker.graphemeCropsQuery(extra), values: [] }, ['local', 'U+4EEE', ...bound, 'local', '仮', 'U+4EEE', ...bound, 60, 0])
    sortFree(graphemePlan)
    assert.ok(graphemePlan.some(d => /SEARCH units USING INDEX unit_family_style \(origin=\? AND family=\?/.test(d)), graphemePlan.join('; '))
    assert.ok(graphemePlan.some(d => /SEARCH units USING INDEX unit_character_style \(origin=\? AND character=\?/.test(d)), graphemePlan.join('; '))
  }
  // A grapheme's counts by style read its two ranges by index and each crop by its id, never the table.
  const graphemeCountPlan = await plan({ sql: worker.graphemeCountsQuery(), values: [] }, ['local', 'U+4EEE', 'local', '仮'])
  assert.ok(!graphemeCountPlan.some(d => /^SCAN units\b/.test(d) && !/USING (COVERING )?INDEX/.test(d)), graphemeCountPlan.join('; '))
  assert.ok(graphemeCountPlan.some(d => /SEARCH units USING (COVERING )?INDEX unit_family/.test(d)), graphemeCountPlan.join('; '))
  await db.prepare("DELETE FROM units WHERE id='apart-crop'").run()
  await db.prepare("DELETE FROM units WHERE id='variant-crop'").run()
  const corpusCountPlan = await plan({ sql: worker.variantCorpusCountsQuery(2), values: [] }, ['假', '反'])
  assert.ok(!corpusCountPlan.some(d => /^SCAN/.test(d)), corpusCountPlan.join('; '))
  // Needs fixing starts from the stored-flagged crops (`unit_state`) and the twice-skipped ones, and
  // looks each up by id; it never reads every crop to work out its review state.
  const attentionPlan = await plan({ sql: `SELECT count(*) AS n FROM units WHERE +origin='local' AND ${worker.attentionCandidatesQuery()}`, values: [] }, [])
  assert.ok(attentionPlan.some(d => /SEARCH units USING (COVERING )?INDEX unit_state \(origin=\? AND state=\?\)/.test(d)), attentionPlan.join('; '))
  assert.ok(!attentionPlan.some(d => /^SCAN units\b/.test(d)), attentionPlan.join('; '))
  // A document's characters are read along the table's own key, and each unit by its id.
  const documentPlan = await plan({ sql: worker.documentCharactersQuery(), values: [] }, ['hk:doc'])
  served(documentPlan, null)
  assert.ok(documentPlan.includes('SEARCH c USING PRIMARY KEY (document=?)'), documentPlan.join('; '))
  assert.ok(documentPlan.some(d => /^SEARCH u USING INDEX sqlite_autoindex_units_1 \(id=\?\)/.test(d)), documentPlan.join('; '))
  // A search of two or more ideographs no alias names finds the characters built from them: 水骨, 氵骨
  // and ⺡骨 are 滑. The rarest component's list is read along its key, each other one looked up by key.
  const built = { char: '滑', code_point: 'U+6ED1', candidates: {} }
  await db.batch([
    db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind('U+6ED1', '滑', '', JSON.stringify(built), JSON.stringify(built)),
    ...[['氵', 0, 7, 'U+6ED1', 1, 1], ['水', 0, 7, 'U+6ED1', 1, 1], ['骨', 0, 7, 'U+6ED1', 1, 1], ['水', 0, 2, 'U+6C38', 1, 0],
      ['骨', 2, 9, 'U+2DC2B', 1, 1]].map(row => db.prepare('INSERT INTO han_components VALUES(?,?,?,?,?,?)').bind(...row)),
    ...[['氵', '氵', 1], ['⺡', '氵', 1], ['水', '水', 2], ['骨', '骨', 2]].map(row => db.prepare('INSERT INTO han_component_names VALUES(?,?,?)').bind(...row))])
  for (const q of ['水骨', '氵骨', '⺡骨', 'U+6C34 U+9AA8']) {
    const found = await (await mf.dispatchFetch(`${base}/layers/suggest?q=${encodeURIComponent(q)}`)).json()
    assert.equal(found.match_kind, 'components', q)
    assert.deepEqual(found.items.map(item => item.char), ['滑'], `${q}: a component character missing from the table is left out`)
  }
  assert.deepEqual((await (await mf.dispatchFetch(`${base}/layers/suggest?q=${encodeURIComponent('骨水骨')}`)).json()).items, [], 'two 骨 are asked for')
  const componentPlan = await plan({ sql: worker.componentMatchQuery(1), values: [] }, ['骨', '水', 1, 1])
  assert.ok(componentPlan.includes('SEARCH han_components USING PRIMARY KEY (component=?)'), componentPlan.join('; '))
  assert.ok(componentPlan.includes('SEARCH o0 USING PRIMARY KEY (component=? AND tier=? AND size=? AND code_point=?)'), componentPlan.join('; '))
  assert.ok(!componentPlan.some(d => d.includes('TEMP B-TREE')), componentPlan.join('; '))
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
    skip_actor: 'CREATE INDEX skip_actor ON skips(actor,at)',
    unit_family_sample: 'CREATE INDEX unit_family_sample ON units(origin,family,shuffle)',
    event_history: "CREATE INDEX event_history ON events(at DESC, id DESC) WHERE kind IN ('review','undo')",
    event_actor_history: "CREATE INDEX event_actor_history ON events(actor, at DESC, id DESC) WHERE kind IN ('review','undo')",
    event_label_history: `CREATE INDEX event_label_history ON events(${worker.historyLabelExpr()}, at DESC, id DESC) WHERE kind IN ('review','undo')`,
  }
  // Pair and trigram frequencies group along their own index, for the collection and for one book. The
  // sort by count is over the grouped rows, which is why the answer is kept at the edge.
  const ngramShapes = [[false, [2], 'unit_ngram_text'], [true, ['hk:doc', 2], 'unit_ngram_document']]
  const ngramServed = (details, index) => {
    assert.ok(details.some(d => new RegExp(`USING (COVERING )?INDEX ${index}\\b`).test(d)), `${index}: ${details.join('; ')}`)
    assert.ok(!details.some(d => d.includes('USE TEMP B-TREE FOR GROUP BY')), details.join('; '))
    assert.ok(!details.some(d => /^SCAN \w+/.test(d) && !/USING (COVERING )?INDEX/.test(d)), details.join('; '))
  }
  for (const [document, bound, index] of ngramShapes) {
    const shape = { sql: worker.ngramsQuery(document), values: [] }
    ngramServed(await plan(shape, bound), index)
    const create = (await db.prepare('SELECT sql FROM sqlite_master WHERE name=?').bind(index).first()).sql
    await db.prepare(`DROP INDEX ${index}`).run()
    // D1 keeps a prepared statement, plan and all, by its text: a trailing space prepares it again.
    await assert.rejects(async () => ngramServed(await plan({ ...shape, sql: shape.sql + ' ' }, bound), index), `the check on ${index} fails without it`)
    await db.prepare(create).run()
  }
  await db.batch([
    db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document,vertical) VALUES('p1',2,'p2',NULL,'申候','hk:doc',1),('p3',2,'p4',NULL,'申候','hk:other',0),
      ('p5',2,'p6',NULL,'候也','hk:doc',0),('p7',2,'p8',NULL,NULL,'hk:doc',1),('p1',3,'p2','p9','申候也','hk:doc',1)`),
  ])
  const countsOf = async query => (await (await mf.dispatchFetch(base + '/atlas/ngrams/' + query)).json()).items
  assert.deepEqual(await countsOf('2'), [{ text: '申候', n: 2, vertical: true }, { text: '候也', n: 1, vertical: false }],
    'pairs are counted by text, most frequent first, and written down the page where half or more of them are')
  assert.deepEqual(await countsOf('2?document=hk%3Aother'), [{ text: '申候', n: 1, vertical: false }], 'a book counts its own pairs')
  assert.deepEqual(await countsOf('3'), [{ text: '申候也', n: 1, vertical: true }], 'trigrams are counted apart from pairs')
  assert.equal((await mf.dispatchFetch(base + '/atlas/ngrams/4')).status, 404, 'only pairs and trigrams are counted')
  // A review that relabels a crop moves the runs it is part of.
  await db.batch([db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document) SELECT a.id,2,b.id,NULL,a.character||b.character,a.document
    FROM units a JOIN units b ON b.id='two' WHERE a.id='one'`)])
  const labelOf = async id => (await db.prepare('SELECT character FROM units WHERE id=?').bind(id).first()).character
  const [firstLabel, secondLabel] = [await labelOf('one'), await labelOf('two')]
  assert.equal((await db.prepare("SELECT text FROM unit_ngrams WHERE first='one'").first()).text, firstLabel + secondLabel)
  await db.prepare("UPDATE units SET character='ヰ' WHERE id='two'").run()
  assert.equal((await db.prepare("SELECT text FROM unit_ngrams WHERE first='one'").first()).text, firstLabel + 'ヰ')
  await db.prepare("UPDATE units SET character=? WHERE id='two'").bind(secondLabel).run()
  // One run's occurrences: read along the run's index in its key order, each crop by its id, never
  // sorted or scanned; a book's through the index it shares with the count.
  const occurrenceServed = (details, index) => {
    assert.ok(details.some(d => new RegExp(`SEARCH p USING (COVERING )?INDEX ${index}\\b`).test(d)), `${index}: ${details.join('; ')}`)
    assert.ok(!details.some(d => d.includes('TEMP B-TREE')), details.join('; '))
    assert.ok(!details.some(d => /^SCAN \w+/.test(d)), details.join('; '))
  }
  for (const [document, bound, index] of [[false, [2, 'ナリ'], 'unit_ngram_text'], [true, ['hk:doc', 2, 'ナリ'], 'unit_ngram_document']]) {
    for (const shape of [{ sql: worker.ngramOccurrencesQuery(document), values: [] }, { sql: worker.ngramCountQuery(document), values: [] }]) {
      const args = shape.sql.includes('LIMIT') ? [...bound, 48, 0] : bound
      occurrenceServed(await plan(shape, args), index)
      const create = (await db.prepare('SELECT sql FROM sqlite_master WHERE name=?').bind(index).first()).sql
      await db.prepare(`DROP INDEX ${index}`).run()
      await assert.rejects(async () => occurrenceServed(await plan({ ...shape, sql: shape.sql + ' ' }, args), index), `the occurrence check on ${index} fails without it`)
      await db.prepare(create).run()
    }
  }
  const pairText = firstLabel + secondLabel
  const occurrences = await (await mf.dispatchFetch(base + '/atlas/ngrams/2/' + encodeURIComponent(pairText))).json()
  assert.equal(occurrences.total, 1, 'a pair counts the occurrences whose crops are both live')
  assert.deepEqual(occurrences.items.map(o => o.crops.map(c => c.id)), [['one', 'two']], 'an occurrence carries its crops in reading order')
  assert.ok(!('context_image' in occurrences.items[0].crops[0]), 'occurrences carry listing fields only')
  // The page around a run is the smallest context render that holds all its crops, clipped to them with
  // a margin of a fifth of the largest crop that stays inside the render.
  const placed = (x, y, context) => ({ crop_box: { x, y, w: 10, h: 10 }, context_image: `/atlas/media/${x}-${y}.webp`, context_box: context })
  assert.deepEqual(worker.ngramPage([placed(50, 50, { x: 0, y: 0, w: 200, h: 200 }), placed(50, 62, { x: 20, y: 20, w: 100, h: 100 })]),
    { image: '/atlas/media/50-62.webp', box: { x: 20, y: 20, w: 100, h: 100 }, region: { x: 48, y: 48, w: 14, h: 26 } })
  assert.deepEqual(worker.ngramPage([placed(21, 21, { x: 20, y: 20, w: 100, h: 100 }), placed(21, 33, { x: 20, y: 20, w: 100, h: 100 })]).region,
    { x: 20, y: 20, w: 13, h: 25 }, 'the margin stays inside the render')
  assert.equal(worker.ngramPage([placed(50, 50, { x: 45, y: 45, w: 20, h: 20 }), placed(50, 70, { x: 45, y: 65, w: 20, h: 20 })]), null, 'no render holds both crops')
  assert.equal(worker.ngramPage([placed(50, 50, { x: 0, y: 0, w: 200, h: 200 }), { crop_box: null }]), null, 'a crop without a box has no page')
  assert.ok('page' in occurrences.items[0] && 'crop_box' in occurrences.items[0].crops[0], 'an occurrence carries its page and its crops\' boxes')
  assert.deepEqual([occurrences.vertical, occurrences.items[0].vertical], [true, true], 'a run is written the way its line is')
  // A trigram is shown only while its third crop is live as well.
  await db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document) VALUES('one',3,'two','gone',?,NULL)`).bind(pairText + '也').run()
  assert.equal((await (await mf.dispatchFetch(base + '/atlas/ngrams/3/' + encodeURIComponent(pairText + '也'))).json()).total, 0, 'a trigram needs its third crop live')
  await db.prepare("UPDATE unit_ngrams SET third='one',text=? WHERE first='one' AND size=3").bind(pairText + firstLabel).run()
  const trigram = await (await mf.dispatchFetch(base + '/atlas/ngrams/3/' + encodeURIComponent(pairText + firstLabel))).json()
  assert.deepEqual([trigram.total, trigram.items.map(o => o.crops.map(c => c.id))], [1, [['one', 'two', 'one']]], 'a trigram carries its three crops')
  assert.equal((await mf.dispatchFetch(base + '/atlas/ngrams/2/' + encodeURIComponent('申候'))).status, 200)
  assert.equal((await (await mf.dispatchFetch(base + '/atlas/ngrams/2/' + encodeURIComponent('申候'))).json()).total, 0, 'pairs whose crops are not live are not shown')
  assert.equal((await mf.dispatchFetch(base + '/atlas/ngrams/2/' + encodeURIComponent(pairText) + '?offset=2001')).status, 404, 'a run does not page past its cap')
  assert.equal((await mf.dispatchFetch(base + '/atlas/ngrams/2/' + encodeURIComponent(pairText) + '?limit=97')).status, 422, 'a page is bounded')
  await db.prepare('DELETE FROM unit_ngrams').run()
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
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    jamo.id, 'local', 'ㅿ', 'ㅿ', 'U+317F', null, 'printed/woodblock', 'other', 'pending', 0, 1, 1, 1,
    JSON.stringify(jamo), JSON.stringify({ character: jamo }), '{}', '{}', null).run()
  await apply('0008_hangul_category.sql')
  assert.deepEqual((await call('/atlas?group=hangul')).items.map(i => i.id), ['hangul'], 'a Hangul label is in the hangul group')
  assert.ok(!(await call('/atlas?group=kana')).items.some(i => i.id === 'hangul'), 'and in no other')
  // A row published as `other` before gugyeol had a category reads `gugyeol` once 0012 has run, and
  // the listing filters it by that group.
  const gugyeol = { id: 'gugyeol', label: '', reading: '', state: 'pending', revision: 0, image_sha256: hash,
    production: 'printed/woodblock', repair: { quiz: true } }
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    gugyeol.id, 'local', '', '', 'U+F67F', null, 'printed/woodblock', 'other', 'pending', 0, 1, 1, 1,
    JSON.stringify(gugyeol), JSON.stringify({ character: gugyeol }), '{}', '{}', null).run()
  await apply('0012_gugyeol_category.sql')
  assert.deepEqual((await call('/atlas?group=gugyeol')).items.map(i => i.id), ['gugyeol'], 'a gugyeol label is in the gugyeol group')
  assert.ok(!(await call('/atlas?group=kana')).items.some(i => i.id === 'gugyeol'), 'and in no other')
  // Explore narrows to one book: the listing counts crops per book, and `document` lists one book's.
  // Crops written straight to D1 and stamped, as a refresh writes them, show in a reviewer's counts.
  for (const [id, book, title] of [['book-a1', 'hl:A', '甲'], ['book-a2', 'hl:A', '甲'], ['book-b1', 'hl:B', '乙']]) {
    const d = { id, label: 'ヌ', reading: 'ヌ', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten',
      source: title, page_id: `${book}:1`, repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ヌ', 'ヌ', 'U+30CC', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', book).run()
  }
  await db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',?)").bind(JSON.stringify('books')).run()
  assert.deepEqual((await call('/atlas?document=hl:A&limit=96')).items.map(i => i.id).sort(), ['book-a1', 'book-a2'], 'one book lists its own crops')
  assert.deepEqual((await call('/atlas', undefined, 200, 'shelf')).documents.filter(b => b.id.startsWith('hl:')).map(({ id, title, total }) => ({ id, title, total })),
    [{ id: 'hl:A', title: '甲', total: 2 }, { id: 'hl:B', title: '乙', total: 1 }], 'the listing counts crops per book, with the title they were published under')
  // The listing files each label under its grapheme, and `grapheme` lists the whole family: 仮 and 假
  // under U+4EEE, and ※, which the character table gives no family, under its own code point, as the
  // publication writes it.
  for (const [id, label, family] of [['kari-1', '仮', 'U+4EEE'], ['kari-2', '假', 'U+4EEE'], ['mark', '※', 'U+203B']]) {
    const d = { id, label, reading: label, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten', repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', label, label, family, null, 'handwritten', 'kanji', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  await db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',?)").bind(JSON.stringify('graphemes')).run()
  const filed = Object.fromEntries((await call('/atlas', undefined, 200, 'shelf')).categories.filter(c => ['仮', '假', '※'].includes(c.label)).map(c => [c.label, c.grapheme]))
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
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    framed.id, 'local', 'カ', 'カ', 'U+30AB', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
    JSON.stringify(framed), JSON.stringify({ character: framed }), '{}', '{}', null).run()
  const wide = { x: 0, y: 0, w: 90, h: 120 }
  const framedRound = { id: crypto.randomUUID(), label: 'カ',
    answers: [{ id: 'framed', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'blank' }] }
  await call('/atlas/rounds', framedRound)
  await db.prepare(`UPDATE units SET data=json_set(data,'$.context_image',json('"/atlas/media/wide.webp"'),'$.context_box',json(?)) WHERE id='framed' AND revision=1`)
    .bind(JSON.stringify(wide)).run()
  await call(`/atlas/rounds/${framedRound.id}/undo`, {})
  const unframed = await call('/atlas/characters/framed')
  assert.deepEqual([unframed.state, unframed.context_image, unframed.context_box], ['pending', '/atlas/media/wide.webp', wide],
    'undo restores the review state and keeps the wider context')
  // GET /atlas/history: every review and undo, newest first, filterable by user or by label; a kind outside
  // ('review','undo') never appears even though its row sits in the same table.
  const get = async path => { const response = await mf.dispatchFetch(base + path); assert.equal(response.status, 200); return response.json() }
  const all = await get('/atlas/history?limit=100')
  assert.ok(all.items.every(i => ['review', 'undo'].includes(i.kind)), 'only review and undo rows are ever listed')
  const sorted = [...all.items].sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : (a.id < b.id ? 1 : -1)))
  assert.deepEqual(all.items.map(i => i.id), sorted.map(i => i.id), 'newest first, tied at ties broken by id')
  const byLabel = await get('/atlas/history?label=%E3%83%A9&limit=100')
  assert.equal(byLabel.items.length, 4, 'two flagged rounds, an inspector review and its undo all read ラ')
  assert.ok(byLabel.items.every(i => i.label === 'ラ'))
  const inspector = users.inspector.id
  const review = byLabel.items.find(i => i.kind === 'review' && i.reviewer.user === inspector)
  assert.deepEqual([review.verdict, review.issue, review.undoes], ['wrong', 'crop', null])
  const undoneEntry = byLabel.items.find(i => i.kind === 'undo')
  assert.deepEqual([undoneEntry.reviewer.user, undoneEntry.verdict, undoneEntry.character, undoneEntry.undoes], [inspector, null, null, review.id],
    'an undo names the review it reverses and leaves the review-only fields null')
  const byActor = await get(`/atlas/history?user=${inspector}&limit=100`)
  assert.equal(byActor.items.length, 2, 'the inspector review and its own undo')
  assert.ok(byActor.items.every(i => i.reviewer.user === inspector))
  // Keyset pagination: a page of one, followed by `before`, walks the same list `limit=100` returned.
  const historyPage1 = await get(`/atlas/history?user=${inspector}&limit=1`)
  assert.equal(historyPage1.items.length, 1)
  assert.ok(historyPage1.next, 'a further page is signalled')
  const historyPage2 = await get(`/atlas/history?user=${inspector}&limit=1&before=${encodeURIComponent(historyPage1.next)}`)
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
  await db.prepare(`INSERT INTO corpus_units(${CORPUS_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?)`).bind('codh:plain', '假', 'U+4EEE', null, 2, 'plain', 0, new TextEncoder().encode(plain).length, 'unknown', 0).run()
  await db.prepare('INSERT INTO corpus_gallery VALUES(?,?,?,?,?)').bind('codh:plain', 2, 'plain', 0, plain).run()
  // Published as a corpus publication publishes it: counted, and the count stamped.
  await db.batch([db.prepare("INSERT INTO corpus_characters VALUES('假','unknown',1,0) ON CONFLICT DO UPDATE SET n=n+1"),
    db.prepare("INSERT OR REPLACE INTO metadata VALUES('corpus_counts_at','\"forms-fixture\"')")])
  // Quick review's per-character counts agree with a recount of the rows after every decision.
  const counted = async () => {
    const kept = (await db.prepare('SELECT character,production,n,named FROM corpus_characters ORDER BY 1,2').all()).results
    const recount = (await db.prepare('SELECT character,production,count(*) AS n,sum(named) AS named FROM corpus_units WHERE character IS NOT NULL GROUP BY 1,2 ORDER BY 1,2').all()).results
    assert.deepEqual(kept, recount, 'corpus_characters follows the forms')
    // The browser's corpus counts follow them too, their cached copy replaced as a decision or reload lands.
    const summed = (await db.prepare('SELECT character,sum(n) AS n FROM corpus_characters GROUP BY 1 HAVING sum(n)>0').all()).results
    const listed = (await call('/atlas/corpus/characters')).items
    assert.deepEqual(listed.map(([label, , n]) => [label, n]), summed.map(r => [r.character, r.n]), 'the browser counts what corpus_characters holds')
    const variant = listed.find(([label]) => label === '假')
    if (variant) assert.equal(variant[1], 'U+4EEE', '假 is filed under its family')
  }
  await counted()
  assert.equal((await call('/atlas/forms/families')).items[0].code_point, 'U+4EEE')
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: 'あ' }, 422)
  const named = await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: '仮' })
  assert.equal(named.count, 2)
  assert.deepEqual(named.undo, [{ kind: 'cluster', cluster: 'U+4EEE:c1', form: null }], 'a cluster never named is cleared again')
  const family = await call('/atlas/forms/families/U%2B4EEE')
  assert.deepEqual([family.assigned, family.items[0].form, family.items[0].assigned, family.items[0].majority, family.items[0].majority_count], [2, '仮', 2, '仮', 2])
  // A cluster of two has no glyphs past its typical twelve. Its least typical glyphs are read
  // backwards along form_unit_cluster, a few rows a cluster.
  assert.deepEqual(family.items[0].unusual, [])
  const leastTypical = (await db.prepare('EXPLAIN QUERY PLAN ' + worker.leastTypicalQuery()).bind('U+4EEE').all()).results.map(r => r.detail)
  assert.ok(leastTypical.some(d => /SEARCH form_units USING INDEX form_unit_cluster \(cluster=\? AND rank>\?\)/.test(d)), leastTypical.join('; '))
  assert.ok(!leastTypical.some(d => /^SCAN|TEMP B-TREE/.test(d)), leastTypical.join('; '))
  assert.equal((await db.prepare("SELECT character FROM corpus_units WHERE id='codh:plain'").first()).character, '仮')
  await counted()
  assert.equal((await call('/atlas/corpus/character?id=codh%3Aplain')).identity_basis, 'form_cluster')
  assert.equal((await call('/atlas/corpus/character?id=codh%3Afixture')).identity_basis, 'human_review', 'a human review outranks a form')
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], form: '假' })
  const record = await call('/atlas/corpus/character?id=codh%3Aplain')
  assert.deepEqual([record.written_character, record.identity_basis], ['假', 'form_glyph'], 'a glyph decision overrides its cluster')
  const shown = (await call('/layers/gallery')).items.find(item => item.id === 'codh:plain')
  assert.deepEqual([shown.written_character, shown.identity_basis, shown.form_cluster], ['假', 'form_glyph', { id: 'U+4EEE:c1' }], 'the gallery shows the form decision')
  const members = await call('/atlas/forms/clusters/U%2B4EEE%3Ac1')
  assert.deepEqual(members.items.map(m => [m.id, m.form, m.basis]), [['codh:plain', '假', 'form_glyph'], ['codh:fixture', '仮', 'form_cluster']])
  await call('/atlas/forms/decisions', { kind: 'inherit', units: ['codh:plain'] })
  assert.equal((await call('/atlas/corpus/character?id=codh%3Aplain')).written_character, '仮', 'following the cluster again')
  // A reload of the same clustering with no new decision still shows once it finishes.
  assert.equal((await call('/atlas/forms/families')).items[0].label, '仮 = 假')
  await db.batch([db.prepare("UPDATE form_families SET label='仮 = 假 = 叚' WHERE code_point='U+4EEE'"),
    db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('forms_loaded_at','\"reloaded\"')")])
  assert.equal((await call('/atlas/forms/families')).items[0].label, '仮 = 假 = 叚', 'a finished reload replaces the cached families')
  await counted()
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: null })
  assert.equal((await db.prepare("SELECT character FROM corpus_units WHERE id='codh:plain'").first()).character, '假',
    'withdrawing the form restores the character the glyph had before')
  await counted()
  // A glyph reported as another character leaves the family: search, counts and the record show
  // that character, the family counts it as rejected, and following the cluster again takes it back.
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], form: '假', issue: 'character' }, 422)
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], issue: 'crop', character: 'テ' }, 422)
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], issue: 'character', character: 'テ' })
  assert.deepEqual({ ...(await db.prepare("SELECT character,family FROM corpus_units WHERE id='codh:plain'").first()) },
    { character: 'テ', family: 'U+30C6' }, 'a glyph reported as テ joins テ\'s family (its own, as this catalogue lacks テ)')
  assert.equal((await call('/atlas/corpus/character?id=codh%3Aplain')).written_character, 'テ')
  const reported = await call('/atlas/forms/families/U%2B4EEE')
  assert.deepEqual([reported.rejected, reported.items[0].rejected], [1, 1])
  assert.equal((await call('/atlas/forms/families')).items[0].rejected, 1)
  assert.deepEqual((await call('/atlas/forms/clusters/U%2B4EEE%3Ac1')).items.map(m => [m.id, m.reported, m.character]),
    [['codh:plain', 'character', 'テ'], ['codh:fixture', null, null]])
  await counted()
  await call('/atlas/forms/decisions', { kind: 'inherit', units: ['codh:plain'] })
  assert.deepEqual({ ...(await db.prepare("SELECT character,family FROM corpus_units WHERE id='codh:plain'").first()) },
    { character: '假', family: 'U+4EEE' }, 'taking the report back restores its character and family')
  assert.equal((await call('/atlas/forms/families/U%2B4EEE')).rejected, 0)
  await counted()
  // A decision answers with the decisions that restore what it changed; sent, they put the rows back.
  const own = await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], issue: 'character', character: 'テ' })
  assert.deepEqual(own.undo, [{ kind: 'inherit', units: ['codh:plain'] }])
  const over = await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain', 'codh:fixture'], form: '假' })
  assert.deepEqual([...over.undo].sort((a, b) => a.kind.localeCompare(b.kind)), [{ kind: 'glyph', form: null, issue: 'character', character: 'テ', units: ['codh:plain'] }, { kind: 'inherit', units: ['codh:fixture'] }])
  for (const decision of over.undo) await call('/atlas/forms/decisions', { ...decision })
  assert.deepEqual((await call('/atlas/forms/clusters/U%2B4EEE%3Ac1')).items.map(m => [m.id, m.reported, m.character, m.basis]),
    [['codh:plain', 'character', 'テ', 'form_glyph'], ['codh:fixture', null, null, null]])
  const mixedCluster = await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', issue: 'mixed' })
  assert.deepEqual(mixedCluster.undo, [{ kind: 'cluster', cluster: 'U+4EEE:c1', form: null }])
  for (const decision of [...own.undo, ...mixedCluster.undo]) await call('/atlas/forms/decisions', { ...decision })
  await counted()
  // A cluster marked mixed names nothing for its glyphs; one reported whole reports every glyph that
  // follows it; clearing the cluster takes either back.
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: '仮' })
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: '仮', issue: 'mixed' }, 422)
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', issue: 'mixed', character: 'テ' }, 422)
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], issue: 'mixed' }, 422)
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', issue: 'mixed' })
  const mixed = await call('/atlas/forms/families/U%2B4EEE')
  assert.deepEqual([mixed.items[0].form, mixed.items[0].issue, mixed.assigned, mixed.rejected], [null, 'mixed', 0, 0])
  assert.equal((await db.prepare("SELECT character FROM corpus_units WHERE id='codh:plain'").first()).character, '假', 'a mixed cluster withdraws its form')
  await counted()
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', issue: 'character', character: 'テ' })
  assert.deepEqual({ ...(await db.prepare("SELECT character,family FROM corpus_units WHERE id='codh:plain'").first()) },
    { character: 'テ', family: 'U+30C6' }, 'a cluster reported as テ reports its glyphs')
  const wholly = await call('/atlas/forms/clusters/U%2B4EEE%3Ac1')
  assert.deepEqual([wholly.issue, wholly.items.map(m => [m.reported, m.character, m.basis])], ['character', [['character', 'テ', 'form_cluster'], ['character', 'テ', 'form_cluster']]])
  assert.deepEqual([(await call('/atlas/forms/families/U%2B4EEE')).rejected, (await call('/atlas/corpus/character?id=codh%3Aplain')).written_character], [2, 'テ'])
  await counted()
  await call('/atlas/forms/decisions', { kind: 'cluster', cluster: 'U+4EEE:c1', form: null })
  assert.deepEqual({ ...(await db.prepare("SELECT character,family FROM corpus_units WHERE id='codh:plain'").first()) },
    { character: '假', family: 'U+4EEE' }, 'clearing the cluster takes its report back')
  assert.deepEqual([(await call('/atlas/forms/families/U%2B4EEE')).rejected, (await call('/atlas/forms/families/U%2B4EEE')).items[0].issue], [0, null])
  await counted()
  const split = await call('/atlas/forms/split/U%2B4EEE%3Ac1?k=2')
  assert.deepEqual(split.groups.map(g => g.ids), [['codh:plain'], ['codh:fixture']])
  assert.equal((await mf.dispatchFetch(base + '/atlas/forms/families/%E0')).status, 404, 'a malformed escape names no family')
  await db.prepare("INSERT INTO form_loading VALUES('now')").run()
  await call('/atlas/forms/decisions', { kind: 'glyph', units: ['codh:plain'], form: '假' }, 503)
  await db.prepare('DELETE FROM form_loading').run()
  const log = await (await mf.dispatchFetch(base + '/atlas/forms/decisions.jsonl')).text()
  assert.equal(log.trim().split('\n').length, 17, 'every accepted decision is logged, the refused ones are not')
  assert.deepEqual(log.trim().split('\n').map(JSON.parse).filter(d => d.issue).map(d => [d.kind, d.issue, d.character]),
    [['glyph', 'character', 'テ'], ['glyph', 'character', 'テ'], ['glyph', 'character', 'テ'], ['cluster', 'mixed', undefined],
      ['cluster', 'mixed', undefined], ['cluster', 'character', 'テ']])
  // A repair verdict changed in place survives a review and its undo, and so does the quiz it decides.
  const vetted = { id: 'vetted', label: 'キ', reading: 'キ', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten',
    repair: { status: 'joined', quiz: true } }
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    vetted.id, 'local', 'キ', 'キ', 'U+30AD', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
    JSON.stringify(vetted), JSON.stringify({ character: vetted }), '{}', '{}', null).run()
  const vettedRound = { id: crypto.randomUUID(), label: 'キ',
    answers: [{ id: 'vetted', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'blank' }] }
  await call('/atlas/rounds', vettedRound)
  await db.prepare(`UPDATE units SET data=json_set(data,'$.repair',json('{"status":"uncertain","quiz":false}')), quiz=0 WHERE id='vetted' AND revision=1`).run()
  await call(`/atlas/rounds/${vettedRound.id}/undo`, {})
  const vettedRow = await db.prepare("SELECT quiz, json_extract(data,'$.repair.quiz') AS dealt, json_extract(data,'$.state') AS state FROM units WHERE id='vetted'").first()
  assert.deepEqual(vettedRow, { quiz: 0, dealt: 0, state: 'pending' }, 'undo restores the review state and keeps the newer repair verdict out of the quiz')
  // A retired crop names the crop that replaced it: deleted, kept for its history, or through a chain.
  const retiredCrop = { id: 'retired-kept', label: 'ア', reading: 'ア', state: 'flagged', revision: 1, image_sha256: hash, production: 'handwritten' }
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
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
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(id, 'local', character, character, family, null,
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
  // A gallery lists running and cursive crops first, then those nobody has judged, then the formal
  // ones, and each style can be asked for alone; the counts by style are taken over the other filters.
  for (const [id, style] of [['sty-a', 'regular'], ['sty-b', 'cursive'], ['sty-c', 'unassessed'], ['sty-d', 'running'], ['sty-e', 'ming']]) {
    const d = { id, label: '仮', reading: '仮', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS},style) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(id, 'local', '仮', '仮', 'U+4EEE', null,
      'handwritten', 'kanji', 'pending', 0, 0, 1, 1, JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null, style).run()
  }
  const styled = async query => { const found = await call('/layers/occurrences?code_point=U%2B4EEE&limit=200' + query); return { found, ids: found.items.map(i => i.id).filter(id => id.startsWith('sty-')) } }
  for (const scope of ['', '&scope=grapheme']) {
    const all = await styled(scope)
    assert.deepEqual(all.ids, ['sty-b', 'sty-d', 'sty-c', 'sty-a', 'sty-e'], 'cursive first, then unassessed, then formal')
    assert.equal(all.found.items.find(i => i.id === 'sty-d').style, 'running', 'a crop carries its style')
    const unassessed = all.found.styles.unassessed
    assert.deepEqual([all.found.styles.cursive, all.found.styles.formal], [2, 2])
    assert.equal(all.found.total, 4 + unassessed)
    const cursive = await styled(scope + '&style=cursive')
    assert.deepEqual(cursive.ids, ['sty-b', 'sty-d'])
    assert.equal(cursive.found.total, 2, 'the total is of the style asked for')
    assert.deepEqual(cursive.found.styles, all.found.styles, 'the counts by style do not depend on the style asked for')
    assert.deepEqual((await styled(scope + '&style=formal')).ids, ['sty-a', 'sty-e'])
  }
  assert.deepEqual((await call('/layers/occurrences?code_point=U%2B4EEE&expand=variants&limit=200&style=cursive')).items.map(i => i.id), ['sty-b', 'sty-d'], 'a widened gallery takes the style too')
  await call('/layers/occurrences?code_point=U%2B4EEE&style=bold', undefined, 422)
  await call('/layers/occurrences?code_point=U%2B4EEE&style=constructor', undefined, 422)
  // A named corpus glyph's row follows its published row's style.
  await db.batch([db.prepare(`INSERT INTO corpus_units(${CORPUS_COLUMNS}) VALUES('sty-corpus','仮','U+4EEE',NULL,9,'none',0,1,'unknown',1)`),
    db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES('sty-corpus','corpus','仮','仮','U+4EEE',NULL,'unknown','kanji','checked',1,0,1,9,'{}','{}','{}','{}',NULL)`)])
  await db.prepare("UPDATE corpus_units SET style='running' WHERE id='sty-corpus'").run()
  assert.equal((await db.prepare("SELECT style FROM units WHERE id='sty-corpus'").first()).style, 'running')
  await db.batch([db.prepare("DELETE FROM units WHERE id='sty-corpus'"), db.prepare("DELETE FROM corpus_units WHERE id='sty-corpus'")])
  await db.prepare("DELETE FROM units WHERE id LIKE 'sty-%'").run()
  // Search finds a crop by its character or by its reading, each once.
  for (const [id, character, reading] of [['find-both', 'とも', 'とも'], ['find-reading', '𪜈', 'とも'], ['find-neither', '𪜈', '𪜈']]) {
    const d = { id, label: character, reading, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(id, 'local', character, reading, null, null,
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
  // A browse listing filtered by character, book or state alone takes its total from those counts.
  await db.batch([db.prepare("UPDATE units SET document='hk:tally' WHERE id IN ('fam-b','fam-c')"), stamp('third refresh')])
  for (const [query, where] of [['', '1=1'], ['reading=仮', "character='仮'"], ['document=hk:tally', "document='hk:tally'"],
    ['state=checked', "state='checked'"], ['reading=仮&document=hk:tally&state=pending', "character='仮' AND document='hk:tally' AND state='pending'"]])
    assert.equal((await call(`/atlas?${encodeURI(query)}&limit=1`)).total, await localCount(where), `browse ${query || 'everything'} totals what it lists`)
  // A crop written without a stamp is not in those counts yet: the listing did not count the table.
  const listedBefore = (await call('/atlas?limit=1')).total, pickedBefore = (await call(`/atlas?${encodeURI('reading=仮')}&limit=1`)).total
  const unstamped = { id: 'unstamped', label: '仮', reading: '仮', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('unstamped', 'local', '仮', '仮', null, null,
    'handwritten', 'kanji', 'pending', 0, 0, 1, 1, JSON.stringify(unstamped), JSON.stringify({ character: unstamped }), '{}', '{}', null).run()
  assert.equal((await call('/atlas?limit=1')).total, listedBefore, 'an unfiltered browse page does not count the table')
  assert.equal((await call(`/atlas?${encodeURI('reading=仮')}&limit=1`)).total, pickedBefore, 'nor does one filtered by character')
  assert.equal((await call('/atlas?group=kanji&limit=1')).total, await localCount("category='kanji'"), 'a listing its counts cannot answer is counted')
  await stamp('fourth refresh').run()
  assert.equal((await call('/atlas?limit=1')).total, listedBefore + 1, 'and the next stamp brings the crop in')
  // A corpus glyph's suggestions are asked for by its source revision, which it has in place of a page hash.
  const corpusSuggestions = (query) => mf.dispatchFetch(`${base}/atlas/characters/${encodeURIComponent('codh:fixture')}/suggestions?${query}`)
  const glyphNow = await call(`/atlas/corpus/character?id=${encodeURIComponent('codh:fixture')}`)
  assert.equal((await corpusSuggestions(`revision=${glyphNow.revision}&source_revision=${glyphNow.source_revision}`)).status, 200, 'a corpus glyph serves suggestions for its source revision')
  assert.equal((await corpusSuggestions(`revision=${glyphNow.revision}&source_revision=${'c'.repeat(64)}`)).status, 409, 'and refuses another')
  // Taking a crop's seen rows out, as removing crops does before the crops, takes its mark with them.
  const seenB = await db.prepare("SELECT * FROM seen WHERE target='seen-b'").all()
  assert.ok(seenB.results.length && await db.prepare("SELECT 1 FROM unit_marks WHERE id='seen-b'").first(), 'seen-b is marked seen')
  await db.prepare("DELETE FROM seen WHERE target='seen-b'").run()
  assert.equal(await db.prepare("SELECT mark FROM unit_marks WHERE id='seen-b'").first(), null, 'a crop whose seen rows are gone is unmarked')
  await db.batch(seenB.results.map(r => db.prepare(`INSERT INTO seen(${Object.keys(r)}) VALUES(${Object.keys(r).map(() => '?')})`).bind(...Object.values(r))))
  // Counts made from the stored states and the moves are the ones working out each crop's state gives.
  const byState = rows => rows.map(r => `${r.label}|${r.document}|${r.state}|${r.n}`).sort()
  for (const review of [true, false])
    for (const reviewer of [null, 'alice', 'bob'])
      for (const character of [null, 'ソ', 'セ']) {
        if (!review && character) continue
        const { where, values } = worker.listingFilter(review, review ? 'not:printed/type' : 'all', character)
        const q = worker.facetsQueries(review, reviewer, where, character !== null)
        const [stored, ...moves] = await Promise.all([q.stored, q.marked, q.skipped].filter(Boolean).map(sql => db.prepare(sql).bind(...values).all()))
        const book = review ? 'NULL' : 'document'
        const whole = await db.prepare(`SELECT character AS label,${book} AS document,${worker.stateFor(reviewer)} AS state,count(*) AS n
          FROM units WHERE ${where.join(' AND ')} GROUP BY 1,2,3`).bind(...values).all()
        const counted = worker.moved(stored.results, moves.flatMap(m => m.results))
        assert.deepEqual(byState(counted), byState(whole.results), `counts for ${review ? 'review' : 'browse'}, ${reviewer ?? 'anyone'}, ${character ?? 'every character'}`)
      }
  // Every mark the triggers kept is the one `skips` and `seen` give from scratch.
  const marks = async sql => (await db.prepare(sql).all()).results.map(r => `${r.id}:${r.mark}`).sort()
  const skippers = "(SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0 WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))"
  const seenHere = "EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0 WHERE s.target=u.id AND s.box IS json_extract(u.data,'$.box'))"
  const kept = await marks('SELECT id,mark FROM unit_marks')
  assert.ok(kept.some(m => m.endsWith(':seen')), 'the run leaves seen crops to compare')
  assert.deepEqual(kept, await marks(`SELECT id,iif(${skippers}>=2,'hard','seen') AS mark FROM units u WHERE ${skippers}>=2 OR ${seenHere}`),
    'the kept marks are what the skips and seen crops say')
  // Every count the triggers kept, crops written anew included, is what counting `units` gives.
  const groups = async sql => (await db.prepare(sql).all()).results.map(r => JSON.stringify(Object.values(r))).sort()
  assert.deepEqual(await groups('SELECT * FROM unit_counts'), await groups(`SELECT origin,coalesce(character,''),coalesce(document,''),state,
    coalesce(family,''),production,quiz,coalesce(json_extract(data,'$.source'),''),count(*) FROM units GROUP BY 1,2,3,4,5,6,7,8`),
    'the kept counts are what the crops say')
  // A batch correction: crops a reader selected across labels, local and corpus, all given one written
  // character in one submission, which is atomic, idempotent and undone as one.
  const countsMatch = async what => assert.deepEqual(await groups('SELECT * FROM unit_counts'), await groups(`SELECT origin,coalesce(character,''),coalesce(document,''),state,
    coalesce(family,''),production,quiz,coalesce(json_extract(data,'$.source'),''),count(*) FROM units GROUP BY 1,2,3,4,5,6,7,8`), what)
  const addLocal = async (id, label, extra = {}) => {
    const d = { id, label, reading: label, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten', repair: { quiz: true }, ...extra }
    await db.prepare(`INSERT INTO units(id,origin,character,reading,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document)
      VALUES(?,'local',?,?,NULL,NULL,'handwritten','kana',?,0,1,1,1,?,?,'{}','{}',NULL)`).bind(id, label, label, d.state, JSON.stringify(d), JSON.stringify({ character: d })).run()
  }
  await addLocal('batch-a', 'ア'); await addLocal('batch-b', 'ウ')
  await addLocal('batch-crop', 'エ', { state: 'flagged', issue: 'crop' })
  await addLocal('batch-same', 'タ'); await addLocal('batch-done', 'タ', { state: 'checked', written_character: 'タ' })
  // Two corpus glyphs: one that can be shown, one whose image the site may not show.
  for (const [id, proxyable, shuffle] of [['codh:batch', true, 3], ['codh:hidden', false, 4]]) {
    const record = JSON.stringify({ id, origin: 'corpus', label: 'ウ', source_label: 'ウ', reading: 'ウ', written_character: 'ウ', identity_status: 'assigned',
      state: 'pending', revision: 0, proxyable, source_revision: sourceRevision })
    await bucket.put(id, record)
    await db.prepare(`INSERT INTO corpus_units(${CORPUS_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?)`).bind(id, 'ウ', 'U+30A6', null, shuffle, id, 0, new TextEncoder().encode(record).length, 'unknown', 0).run()
  }
  const localCrop = id => ({ id, revision: 0, image_sha256: hash })
  const corpusCrop = id => ({ id, revision: 0, source_revision: sourceRevision })
  const legacyNow = await call('/atlas/corpus/character?id=' + encodeURIComponent('codh:legacy'))
  const refusedBatch = await call('/atlas/corrections', { id: crypto.randomUUID(), character: 'タ',
    crops: [localCrop('batch-a'), { ...localCrop('batch-b'), revision: 7 }, corpusCrop('codh:hidden'), { id: 'codh:legacy', revision: legacyNow.revision, source_revision: legacyNow.source_revision }] }, 409)
  assert.deepEqual(refusedBatch.targets, [{ id: 'batch-b', reason: 'changed' }, { id: 'codh:hidden', reason: 'unavailable' }, { id: 'codh:legacy', reason: 'checked' }],
    'a batch names every crop it refuses: changed, not showable, or checked by a person as another character')
  assert.equal((await call('/atlas/characters/batch-a')).revision, 0, 'and saves none of them')
  await call('/atlas/corrections', { id: crypto.randomUUID(), character: 'タ', crops: [localCrop('batch-a')], seen: [] }, 422)
  await call('/atlas/corrections', { id: crypto.randomUUID(), character: 'タ', crops: Array.from({ length: 145 }, (_, i) => localCrop('x' + i)) }, 422)
  for (const character of ['‍', '́', ' ', '\u0007', 'タナ'])
    await call('/atlas/corrections', { id: crypto.randomUUID(), character, crops: [localCrop('batch-a')] }, 422)
  const batchReq = { id: crypto.randomUUID(), character: 'タ',
    crops: [localCrop('batch-a'), localCrop('batch-b'), localCrop('batch-crop'), localCrop('batch-same'), localCrop('batch-done'), corpusCrop('codh:batch')] }
  const corrected = await call('/atlas/corrections', batchReq)
  assert.deepEqual(corrected.results.map(r => [r.target_id, r.state]).sort(), [['batch-a', 'checked'], ['batch-b', 'checked'], ['batch-crop', 'flagged'], ['batch-same', 'checked'], ['codh:batch', 'checked']])
  assert.deepEqual(corrected.unchanged, ['batch-done'], 'a crop a person already checked as the character is left as it is')
  assert.deepEqual(await call('/atlas/corrections', batchReq), corrected, 'a retried batch returns the first result')
  await call('/atlas/corrections', { ...batchReq, character: 'ナ' }, 409)
  for (const id of ['batch-a', 'batch-b', 'batch-same']) {
    const row = await call('/atlas/characters/' + id)
    assert.deepEqual([row.label, row.state], ['タ', 'checked'], id + ' is タ, checked')
  }
  const cropRow = await call('/atlas/characters/batch-crop')
  assert.deepEqual([cropRow.label, cropRow.state, cropRow.issue], ['タ', 'flagged', 'crop'], 'a crop reported for its box keeps the report')
  assert.equal((await call('/atlas/corpus/character?id=' + encodeURIComponent('codh:batch'))).label, 'タ', 'the corpus glyph takes the character too')
  const batchEvidence = (await db.prepare(`SELECT json_extract(event,'$.evidence') AS e FROM events WHERE submission=?`).bind(users.integration.id + ':' + batchReq.id).all()).results.map(r => JSON.parse(r.e))
  assert.ok(batchEvidence.every(e => e.kind === 'character-review' && e.batch === batchReq.id && e.request === undefined), 'a batch is recorded as inspector reviews carrying its id, not its request')
  assert.deepEqual(batchEvidence.map(e => e.verdict).sort(), ['match', 'wrong', 'wrong', 'wrong', 'wrong'], 'a crop already written as the character is confirmed')
  const batchHistory = (await call('/atlas/history?limit=10')).items.filter(item => item.batch === batchReq.id)
  assert.equal(batchHistory.length, 5, 'History names the batch of each edit')
  await countsMatch('the counts follow a batch')
  await call(`/atlas/corrections/${batchReq.id}/undo`, {})
  assert.deepEqual([(await call('/atlas/characters/batch-a')).label, (await call('/atlas/characters/batch-b')).label, (await call('/atlas/characters/batch-crop')).issue], ['ア', 'ウ', 'crop'], 'undo restores every crop')
  await countsMatch('the counts follow its undo')
  // A full batch of crops with real-length ids stays well inside D1's row limit.
  const manyIds = Array.from({ length: 144 }, (_, i) => 'ex:0b3fffde4433fda4:' + createHash('sha1').update('batch' + i).digest('hex').slice(0, 20))
  for (const id of manyIds) await addLocal(id, 'サ')
  const fullBatch = { id: crypto.randomUUID(), character: 'セ', crops: manyIds.map(localCrop) }
  assert.equal((await call('/atlas/corrections', fullBatch)).results.length, 144)
  const storedSize = await db.prepare('SELECT length(request)+length(response) AS n FROM submissions WHERE id=?').bind(users.integration.id + ':' + fullBatch.id).first()
  assert.ok(storedSize.n < 100000, `a 144-crop batchReq is stored in ${storedSize.n} bytes`)
  const eventSize = await db.prepare('SELECT max(length(event)+length(before_data)+length(after_data)+length(snapshot)) AS n FROM events WHERE submission=?').bind(users.integration.id + ':' + fullBatch.id).first()
  assert.ok(eventSize.n < 20000, `and each of its events in at most ${eventSize.n} bytes`)
  await countsMatch('the counts follow a full batch')
  // A full round of wrong answers, with real-length ids and notes, stays well inside D1's row limit
  // too: the round's request is kept once, on its submission, and each event carries its own answer.
  const roundIds = Array.from({ length: 144 }, (_, i) => 'ex:0b3fffde4433fda4:' + createHash('sha1').update('round' + i).digest('hex').slice(0, 20))
  for (const id of roundIds) await addLocal(id, 'ソ')
  const fullRound = { id: crypto.randomUUID(), label: 'ソ', seen: [], skipped: [],
    answers: roundIds.map(id => ({ ...localCrop(id), verdict: 'wrong', issue: 'character', character: 'ン', note: '点の向きがンに見える' })) }
  const firstRound = await call('/atlas/rounds', fullRound)
  assert.equal(firstRound.results.length, 144)
  const roundSize = await db.prepare('SELECT length(request)+length(response) AS n FROM submissions WHERE id=?').bind(users.integration.id + ':' + fullRound.id).first()
  assert.ok(roundSize.n < 100000, `a 144-answer round is stored in ${roundSize.n} bytes`)
  const roundEvent = await db.prepare('SELECT max(length(event)+length(before_data)+length(after_data)+length(snapshot)) AS n FROM events WHERE submission=?').bind(users.integration.id + ':' + fullRound.id).first()
  assert.ok(roundEvent.n < 20000, `and each of its events in at most ${roundEvent.n} bytes`)
  const roundEvidence = (await db.prepare(`SELECT json_extract(event,'$.evidence') AS e, target FROM events WHERE submission=?`).bind(users.integration.id + ':' + fullRound.id).all()).results
  assert.ok(roundEvidence.every(r => { const e = JSON.parse(r.e); return e.round === fullRound.id && e.request === undefined && e.answer?.id === r.target }),
    'each round event names its round and carries only its own answer')
  assert.deepEqual(await call('/atlas/rounds', fullRound), firstRound, 'a retried round returns the first result')
  await countsMatch('the counts follow a full round')
  // A written form: the crop keeps its character, state and revision, and every listing shows it.
  await addLocal('form-local', '還')
  const formPath = '/atlas/characters/form-local/written-form'
  const formSave = { id: crypto.randomUUID(), revision: 0, image_sha256: hash, form: '⿺辶𦊷' }
  const formed = await call(formPath, formSave)
  assert.deepEqual([formed.written_form, formed.label, formed.state, formed.revision], ['⿺辶𦊷', '還', 'pending', 0])
  assert.equal((await call('/atlas/characters/form-local')).written_form, '⿺辶𦊷', 'the inspector reads it')
  assert.equal((await call('/atlas?reading=' + encodeURIComponent('還'))).items.find(item => item.id === 'form-local').written_form, '⿺辶𦊷', 'a listing reads it')
  assert.equal((await call(formPath, formSave)).written_form, '⿺辶𦊷', 'a retry answers with the crop')
  await call(formPath, { ...formSave, form: '𮟃' }, 409)
  for (const form of ['⿺辶', '⿰木木木', '還還', 'a⿰', '⿰木a', ' '.repeat(3) + '⿰'])
    await call(formPath, { ...formSave, id: crypto.randomUUID(), form }, 422)
  await call(formPath, { ...formSave, id: crypto.randomUUID(), revision: 1 }, 409)
  await call(formPath, { ...formSave, id: crypto.randomUUID(), image_sha256: 'c'.repeat(64) }, 409)
  const variant = await call(formPath, { ...formSave, id: crypto.randomUUID(), form: 'U+2E7C3' })
  assert.equal(variant.written_form, '𮟃', 'a code point is read as its character')
  // A review saved against the revision the crop was opened at still stands, and keeps the form.
  await call('/atlas/characters/form-local', { id: crypto.randomUUID(), revision: 0, image_sha256: hash, verdict: 'match', issue: 'reading' })
  const reviewedForm = await call('/atlas/characters/form-local')
  assert.deepEqual([reviewedForm.state, reviewedForm.revision, reviewedForm.written_form], ['checked', 1, '𮟃'])
  assert.equal((await call(formPath, { ...formSave, id: crypto.randomUUID(), revision: 1, form: '還' })).written_form, null, 'its own character clears it')
  // A corpus glyph nothing has named gets its `units` row, and stays unflagged and unreviewed.
  const glyphForm = await call('/atlas/corpus/written-forms', { id: crypto.randomUUID(), identity: 'na-5',
    revision: 0, source_revision: createHash('sha256').update('na-5').digest('hex'), form: '⿱十乚' })
  assert.deepEqual([glyphForm.written_form, glyphForm.label, glyphForm.state, glyphForm.revision], ['⿱十乚', 'ナ', 'pending', 0])
  assert.deepEqual(await db.prepare("SELECT origin,state,written_form FROM units WHERE id='na-5'").first(), { origin: 'corpus', state: 'pending', written_form: '⿱十乚' })
  assert.equal((await call('/atlas/corpus/character?id=na-5')).written_form, '⿱十乚')
  const naRow = { char: 'ナ', code_point: 'U+30CA', candidates: {} }
  await db.prepare('INSERT OR IGNORE INTO characters VALUES(?,?,?,?,?)').bind('U+30CA', 'ナ', '', JSON.stringify(naRow), JSON.stringify(naRow)).run()
  assert.equal((await call('/layers/candidates?code_point=U%2B30CA&limit=200')).glyph_items.find(item => item.id === 'na-5')?.written_form, '⿱十乚',
    'a corpus gallery reads it')
  assert.equal((await db.prepare("SELECT count(*) AS n FROM events WHERE target IN ('na-5','form-local') AND json_extract(json_extract(event,'$.evidence'),'$.kind')!='character-review'").first()).n, 0,
    'a written form writes no review event')
  const formExport = await call('/atlas/written-forms')
  assert.equal(formExport.kind, 'atlas-written-forms')
  assert.deepEqual(formExport.forms.map(f => [f.target, f.form, f.revision, f.current]),
    [['form-local', '⿺辶𦊷', 0, false], ['form-local', '𮟃', 0, false], ['form-local', null, 1, true], ['na-5', '⿱十乚', 0, true]])
  assert.deepEqual([formExport.forms[0].label, formExport.forms[0].pixels, formExport.forms[0].origin, formExport.forms[3].origin], ['還', hash, 'local', 'corpus'])
  const formPlan = (await db.prepare('EXPLAIN QUERY PLAN ' + worker.writtenFormsQuery()).all()).results.map(row => row.detail).join(' | ')
  assert.ok(!/SCAN l\b/.test(formPlan), 'each form finds its crop\'s latest through the index: ' + formPlan)
  // One address gets 30 written forms a minute.
  let formsLimited = false
  for (let i = 0; i < 40 && !formsLimited; i++) {
    const response = await mf.dispatchFetch(base + formPath, { method: 'POST', headers: { 'content-type': 'application/json', origin: base, cookie: users.integration.cookie }, body: '{}' })
    formsLimited = response.status === 429
  }
  assert.ok(formsLimited, 'written forms are rate-limited per address')
  // One address gets 20 batches a minute.
  let rateLimited = false
  for (let i = 0; i < 25 && !rateLimited; i++) {
    const response = await mf.dispatchFetch(base + '/atlas/corrections', { method: 'POST', headers: { 'content-type': 'application/json', origin: base, cookie: users.integration.cookie }, body: '{}' })
    rateLimited = response.status === 429
  }
  assert.ok(rateLimited, 'batches are rate-rateLimited per address')
  console.log('Workerd integration passed: atomic rounds, issue-only saves, retries, undo, corpus identity, search, gallery, export, seen crops, flagged order, corpus rounds, edit history, hosted forms, batch corrections, written forms.')
} finally {
  await mf.dispose()
  await rm(bundleDir, { recursive: true, force: true })
}
