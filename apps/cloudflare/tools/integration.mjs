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
    // The ledger and form sections make more claims than the site's 30 a minute; the limit itself is checked at the end.
    CLAIMS: { namespace_id: '4403', simple: { limit: 100, period: 60 } } },
}]}))
try {
  const db = await mf.getD1Database('DB')
  // The columns a fixture row fills; later ones (`style`) take their defaults.
  const UNIT_COLUMNS = 'id,origin,character,reading,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document'
  // The columns a crop is written with once 0056 left `reading` unread.
  const CROP_COLUMNS = UNIT_COLUMNS.replace(',reading,', ',')
  const CORPUS_COLUMNS = 'id,character,family,visual_group,shuffle,object,offset,size,production,named'
  // Every migration, in order, the way a new deployment applies them. Rows published and reviewed
  // before 0006 are written first, to show what it makes of them.
  const migrations = (await readdir(new URL('../migrations/', import.meta.url))).filter(name => name.endsWith('.sql')).sort()
  const apply = async name => {
    const schema = await readFile(new URL(`../migrations/${name}`, import.meta.url), 'utf8')
    // Comments go first: a comment line that starts with a keyword would otherwise read as a statement.
    const statements = schema.replace(/^\s*--.*$/gm, '').match(/CREATE TRIGGER[\s\S]*?\nEND;|(?:CREATE (?:TABLE|(?:UNIQUE )?INDEX)|DROP (?:TRIGGER|INDEX|TABLE)|UPDATE|ALTER TABLE|DELETE FROM|INSERT INTO) [\s\S]*?;/g)
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
  for (const name of migrations.filter(name => name >= '0010' && name < '0055')) await apply(name)
  // A crop flagged as another character before 0055 names the issue `reading`.
  const flaggedBefore = { id: 'flagged-before', label: 'ト', reading: 'ト', state: 'flagged', issue: 'reading', revision: 1 }
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('flagged-before', 'local', 'ト', 'ト', null, null,
    'printed', 'kana', 'flagged', 1, 0, 1, 2, JSON.stringify(flaggedBefore), JSON.stringify({ character: flaggedBefore }), '{}', '{}', null).run()
  for (const name of migrations.filter(name => name >= '0055')) await apply(name)
  assert.equal((await db.prepare("SELECT json_extract(data,'$.issue') AS issue FROM units WHERE id='flagged-before'").first()).issue, 'character',
    'a crop flagged under the old issue name carries the wrong-character issue')
  assert.ok(!(await db.prepare("SELECT sql FROM sqlite_master WHERE name='event_apply'").first()).sql.includes('reading'),
    'the event trigger writes no reading')
  assert.equal(await db.prepare("SELECT 1 FROM sqlite_master WHERE name='unit_reading'").first(), null, 'the reading index is gone')
  await db.prepare("DELETE FROM units WHERE id='flagged-before'").run()
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
    const d = { id, label: 'ア', state: 'pending', revision: 0, image_sha256: hash,
      production: 'handwritten', repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ア', 'U+3042', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  // Each row names its family and the family's members, as the character table writes them.
  const families = { 'U+4EEE': ['仮', '假'], 'U+3042': ['あ', 'ア'] }
  for (const [head, chars] of Object.entries(families)) {
    const grapheme = { code_point: head, char: chars[0], members: chars.map(c => ({ char: c, code_point: 'U+' + c.codePointAt(0).toString(16).toUpperCase() })) }
    for (const { char, code_point } of grapheme.members) {
      const data = { char, code_point, grapheme, candidates: {} }
      await db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind(code_point, char, '', JSON.stringify(data), JSON.stringify(data)).run()
    }
  }
  const corpus = { id: 'codh:fixture', origin: 'corpus', label: '仮', source_label: '仮',
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
  const round = { id: crypto.randomUUID(), grapheme: 'U+3042', answers: [decision] }
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
  // A review saved before 0056 recorded the crop with its reading; undoing it restores the crop without one.
  await db.prepare("UPDATE events SET before_data=json_set(before_data,'$.reading','ア') WHERE target='one'").run()
  await call(`/atlas/rounds/${round.id}/undo`, {})
  assert.equal((await db.prepare("SELECT json_type(data,'$.reading') AS kept FROM units WHERE id='one'").first()).kept, null,
    'an undo restores no reading')
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
    const d = { id, label: 'ラ', state: 'pending', revision: 0, image_sha256: hash,
      production: 'handwritten', repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ラ', 'U+3042', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const flagRound = { id: crypto.randomUUID(), grapheme: 'U+30E9', answers: [
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
  const flaggedOrder = async () => (await call('/atlas?character=ラ&state=flagged')).items.map(i => i.id)
  assert.deepEqual(await flaggedOrder(), ['flag-a', 'flag-b'], 'flagged crops nobody has reviewed keep their shuffled order')
  const inspected = { id: crypto.randomUUID(), revision: 1,
    image_sha256: hash, verdict: 'wrong', issue: 'crop' }
  await call('/atlas/characters/flag-a', inspected, 200, 'inspector')
  assert.deepEqual(await flaggedOrder(), ['flag-b', 'flag-a'], 'a crop reviewed in the inspector moves behind one nobody has looked at')
  // `reported=hide` leaves the crop reviewed in the inspector out, and counts it; `reported=show`,
  // the default, leaves every other caller unaffected.
  const flagged = async (reported) => call('/atlas?character=ラ&state=flagged' + (reported ? `&reported=${reported}` : ''))
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
  const ligatureCorrection = { id: crypto.randomUUID(), revision: 0,
    image_sha256: hash, verdict: 'wrong', issue: 'character', character: '𪜈' }
  await call('/atlas/characters/two', ligatureCorrection)
  assert.equal((await call('/atlas/characters/two')).label, '𪜈', 'a ligature is one written character')
  await call('/atlas/characters/one', { ...ligatureCorrection, id: crypto.randomUUID(), revision: (await call('/atlas/characters/one')).revision,
    issue: 'crop', character: undefined, correction: 'ア' }, 422) // typed characters belong to a joined crop
  const renamed = (await call('/atlas/documents/hk%3Aother/characters')).characters[0]
  assert.deepEqual([renamed.label, renamed.source], ['𪜈', 'review'], 'a label corrected on the site is the review\'s')
  const moved = { ...correction, id: crypto.randomUUID(), revision: 1, character: '𪜈' }
  await call('/atlas/corpus/reviews', moved)
  assert.equal((await call('/layers/candidates?code_point=U%2B2A708&scope=grapheme')).family_total, 1)
  assert.equal((await call('/layers/candidates?code_point=U%2B4EEE&scope=grapheme')).family_total, 0)
  // Seen crops: a round may record the crops it showed and left unflagged, and they leave the queue.
  for (const id of ['seen-a', 'seen-b', 'seen-c']) {
    const d = { id, label: 'セ', state: 'pending', revision: 0, image_sha256: hash,
      image: `/atlas/media/${id}.webp`, production: 'handwritten', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'セ', 'U+30BB', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  // Browse counts are cached per version of the data, so each write below must show in them at once.
  const browseSe = async () => { const { pending, seen, flagged } = (await call('/atlas')).categories.find(c => c.label === 'セ'); return { pending, seen, flagged } }
  assert.deepEqual(await browseSe(), { pending: 3, seen: 0, flagged: 0 })
  const pendingSe = async () => (await call('/atlas?purpose=review&grapheme=U%2B30BB&state=pending&limit=96')).items.map(i => i.id).sort()
  const passed = { id: crypto.randomUUID(), grapheme: 'U+30BB',
    seen: [{ id: 'seen-a', image_sha256: hash }, { id: 'seen-b', image_sha256: hash }, { id: 'seen-c', image_sha256: 'c'.repeat(64) }] }
  const recorded = await call('/atlas/rounds', passed)
  assert.equal(recorded.results.filter(r => r.field === 'seen').length, 2, 'a crop whose pixels changed is skipped')
  assert.deepEqual(await call('/atlas/rounds', passed), recorded, 'a retried pass is the same pass')
  const scrolled = { ...passed, seen: [...passed.seen, { id: 'seen-extra', image_sha256: hash }] }
  assert.deepEqual(await call('/atlas/rounds', scrolled), recorded, 'a retry with more crops on screen returns the first result')
  await call('/atlas/rounds', { id: crypto.randomUUID(), seen: [{ id: 'seen-a', image_sha256: hash }] }, 422)
  assert.deepEqual(await pendingSe(), ['seen-c'], 'seen crops leave the queue')
  assert.deepEqual(await browseSe(), { pending: 1, seen: 2, flagged: 0 }, 'a round shows in the browse counts')
  const summary = await call('/atlas?purpose=review&grapheme=U%2B30BB')
  assert.equal(summary.counts.seen, 2)
  assert.equal(summary.items.find(i => i.id === 'seen-a').state, 'seen')
  assert.equal((await call('/atlas/characters/seen-a')).revision, 0, 'seeing a crop changes nothing about it')
  assert.ok(!(await call('/atlas/reviews.json?include_processed=true')).reviews.some(r => r.event?.target_id?.startsWith('seen-')), 'seen is not a review')
  const flagOnSeen = { id: crypto.randomUUID(), grapheme: 'U+30BB',
    answers: [{ id: 'seen-a', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'crop' }], seen: [{ id: 'seen-c', image_sha256: hash }] }
  await call('/atlas/rounds', flagOnSeen, 200, 'second')
  assert.equal((await call('/atlas/characters/seen-a')).state, 'flagged', 'a seen crop can still be flagged at its revision')
  assert.deepEqual(await pendingSe(), [], 'answers and seen crops save together')
  assert.deepEqual(await browseSe(), { pending: 0, seen: 2, flagged: 1 })
  await call(`/atlas/rounds/${passed.id}/undo`, {})
  assert.deepEqual(await pendingSe(), ['seen-b'], 'undoing a pass returns its crops to the queue')
  assert.deepEqual(await browseSe(), { pending: 1, seen: 1, flagged: 1 }, 'undoing a round with no reviews shows in the browse counts')
  await call('/atlas/rounds', { id: crypto.randomUUID(), grapheme: 'U+30BB', answers: [], seen: [] }, 422)
  // A crop re-cut after the round was dealt shows another image; the reader never saw that one.
  const recut = await call('/atlas/rounds', { id: crypto.randomUUID(), grapheme: 'U+30BB',
    seen: [{ id: 'seen-b', image_sha256: hash, image: '/atlas/media/an-older-cut.webp' }] })
  assert.equal(recut.results.filter(r => r.field === 'seen').length, 0, 'a crop re-cut since the round was dealt is not seen')
  const dealt = await call('/atlas/rounds', { id: crypto.randomUUID(), grapheme: 'U+30BB',
    seen: [{ id: 'seen-b', image_sha256: hash, image: '/atlas/media/seen-b.webp' }] })
  assert.equal(dealt.results.filter(r => r.field === 'seen').length, 1, 'the crop the round showed is seen')
  // Skipped crops: recorded against the reviewer, dealt first to others, rested for the one who
  // skipped, hard once two reviewers skipped them, and taken back by an undo.
  for (const id of ['skip-a', 'skip-b', 'skip-c']) {
    const d = { id, label: 'ソ', state: 'pending', revision: 0, image_sha256: hash,
      production: 'handwritten', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ソ', 'U+30BD', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const dealtTo = async reviewer => (await call('/atlas?purpose=review&grapheme=U%2B30BD&state=pending&seed=3&limit=96', undefined, 200, reviewer)).items.map(i => i.id)
  const skipBy = id => ({ id: crypto.randomUUID(), grapheme: 'U+30BD', skipped: [{ id, image_sha256: hash }] })
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
  assert.deepEqual((await call('/atlas?state=hard&character=ソ')).items.map(i => i.id), ['skip-b'], 'two skips make a crop hard')
  assert.ok((await call('/atlas?state=attention&character=ソ')).items.some(i => i.id === 'skip-b'), 'the Flagged view lists hard crops')
  assert.ok(!(await dealtTo('carol')).includes('skip-b'), 'a hard crop leaves the rounds')
  // A crop is hard at the box it was skipped at, whoever moves the box: a refresh updating it in place,
  // or a publication writing the crop anew.
  const hardSo = async () => (await call('/atlas?state=hard&character=ソ')).items.map(i => i.id)
  const moveBox = box => db.prepare("UPDATE units SET data=json_set(data,'$.box',json(?)) WHERE id='skip-b'").bind(box).run()
  await moveBox('{"x":9,"y":2,"w":3,"h":4}')
  assert.deepEqual(await hardSo(), [], 'a crop moved to another box is not hard there')
  await moveBox('{"x":1,"y":2,"w":3,"h":4}')
  assert.deepEqual(await hardSo(), ['skip-b'], 'and is again once it is back')
  // A generated column (`style_order`, `hand_order`, `crop_version`) takes no value.
  const { style_order: _order, hand_order: _hand, crop_version: _version, ...skipB } = await db.prepare("SELECT * FROM units WHERE id='skip-b'").first()
  const rewrite = data => db.prepare(`INSERT OR REPLACE INTO units VALUES(${Object.keys(skipB).map(() => '?').join(',')})`).bind(...Object.values({ ...skipB, data })).run()
  await rewrite(JSON.stringify({ ...JSON.parse(skipB.data), box: { x: 7, y: 2, w: 3, h: 4 } }))
  assert.deepEqual(await hardSo(), [], 'nor is a crop written anew at another box')
  await rewrite(skipB.data)
  assert.deepEqual(await hardSo(), ['skip-b'])
  await call(`/atlas/rounds/${second.id}/undo`, {}, 200, 'bob')
  assert.deepEqual((await call('/atlas?state=hard&character=ソ')).items, [], 'an undo takes a skip back')
  // Corpus glyphs in Quick review: a character's local crops first, then its assigned, proxyable
  // corpus glyphs, until a round names them and they become `units` rows like any other crop.
  const worker = await import(bundle)
  const glyph = (id, fields = {}) => ({ id, origin: 'corpus', label: 'ナ', char: 'ナ', written_character: 'ナ',
    identity_status: 'assigned', source_label: 'ナ', grapheme: 'U+30CA', state: 'pending', revision: 0,
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
    const d = { id, label: 'ナ', state: 'pending', revision: 0, image_sha256: hash,
      production: 'handwritten', box: { x: 1, y: 2, w: 3, h: 4 }, repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ナ', 'U+30CA', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const roundOf = async (params = '', as = null) => call(`/atlas?purpose=review&grapheme=U%2B30CA&state=pending${params.includes('limit=') ? '' : '&limit=96'}${params}`, undefined, 200, as)
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
  await call('/atlas?purpose=review&grapheme=U%2B30CA&offset=5000', undefined, 404)
  assert.ok(!dealtNa.includes('na-movable') && !dealtNa.includes('na-unassigned'), 'movable type and unassigned glyphs are not dealt')
  assert.ok((await ids('&seed=0&production=all')).includes('na-movable'), 'every material includes movable type')
  assert.deepEqual((await ids('&seed=0&production=printed/woodblock')), ['na-2', 'na-4', 'na-5', 'na-3', 'na-1'], 'one material deals only its glyphs')
  assert.deepEqual((await ids('&seed=0&production=printed/type')), ['na-movable'], 'a node deals what lies under it')
  assert.deepEqual((await ids('&seed=0&production=printed')), ['na-2', 'na-movable', 'na-4', 'na-5', 'na-3', 'na-1'], 'a wider node merges its productions in shuffle order')
  assert.deepEqual((await ids('&seed=0&production=inscribed')), [], 'a node the character has nothing under deals nothing')
  await call('/atlas?purpose=review&grapheme=U%2B30CA&production=printed%20type', undefined, 400)
  await call('/atlas?purpose=review&grapheme=U%2B30CA&production=not:all', undefined, 400)
  await call('/atlas?purpose=review&grapheme=U%2B30CA&production=printed/typo', undefined, 400)
  assert.deepEqual((await call('/atlas?purpose=review&grapheme=U%2B30CC&state=pending')).items.map(i => i.id), ['nu-shown'], 'a glyph this site may not serve is not dealt')
  // A glyph passed over still takes its position: the next offset runs ahead of the items, and once
  // the glyphs run out the total is what there was to deal.
  const passedOver = await call('/atlas?purpose=review&grapheme=U%2B30CC&state=pending&limit=1')
  assert.deepEqual([passedOver.items.length, passedOver.next_offset, passedOver.total], [0, 1, 2])
  const rest = await call('/atlas?purpose=review&grapheme=U%2B30CC&state=pending&limit=2&offset=1')
  assert.deepEqual([rest.items.map(i => i.id), rest.next_offset, rest.total], [['nu-shown'], 2, 2])
  const first = (await roundOf('&seed=0')).items.find(i => i.id === 'na-2')
  assert.equal(first.origin, 'corpus')
  assert.equal(first.source.title, 'A woodblock book', 'a corpus tile can name its source')
  const category = async (as = null) => (await call('/atlas?purpose=review&limit=1', undefined, 200, as)).categories.find(c => c.label === 'ナ')
  assert.deepEqual(await category(), { label: 'ナ', grapheme: 'U+30CA', total: 7, pending: 7, seen: 0, checked: 0, flagged: 0, hard: 0, skipped: 0 }, 'counts include corpus glyphs')
  assert.equal((await call('/atlas?purpose=review&limit=1&production=all')).categories.find(c => c.label === 'ナ').pending, 8)
  const na = Object.fromEntries((await roundOf('&seed=0')).items.map(i => [i.id, i]))
  const cropRound = { id: crypto.randomUUID(), grapheme: 'U+30CA',
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
  const refused = await call('/atlas/rounds', { id: crypto.randomUUID(), grapheme: 'U+30CC',
    seen: [{ id: 'nu-private', source_revision: createHash('sha256').update('nu-private').digest('hex') }] }, 200, 'alice')
  assert.equal(refused.results.length, 0)
  await call('/atlas/rounds', { id: crypto.randomUUID(), grapheme: 'U+30CA', answers: [{ id: 'na-unassigned', revision: 0,
    source_revision: createHash('sha256').update('na-unassigned').digest('hex'), verdict: 'wrong', issue: 'crop' }] }, 409, 'alice')
  // A round deals a grapheme: every character the character table files under it, the local crops
  // first, then the characters' corpus glyphs merged in shuffle order. A character held only as corpus
  // glyphs is counted under its family's grapheme.
  const paired = { code_point: 'U+30CA', char: 'ナ', members: [{ char: 'ナ', code_point: 'U+30CA' }, { char: 'ヌ', code_point: 'U+30CC' }] }
  await db.batch([...paired.members.map(({ char, code_point }) => {
    const data = JSON.stringify({ char, code_point, grapheme: paired, candidates: {} })
    return db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind(code_point, char, '', data, data)
  }), db.prepare("INSERT OR REPLACE INTO metadata VALUES('corpus_counts_at','\"paired-graphemes\"')")])
  const pairedRound = await roundOf('&seed=0', 'carol')
  assert.deepEqual(pairedRound.grapheme, { code_point: 'U+30CA', char: 'ナ', members: ['ナ', 'ヌ'] }, 'a round names its grapheme and members')
  assert.deepEqual(pairedRound.items.slice(0, 2).map(i => i.id).sort(), ['na-local-a', 'na-local-b'], 'local crops come first')
  assert.deepEqual(pairedRound.items.slice(2).map(i => i.id).sort(), ['na-1', 'na-2', 'na-3', 'na-4', 'na-5', 'nu-shown'], 'then the corpus glyphs of both characters')
  assert.deepEqual(pairedRound.categories.map(c => c.label).sort(), ['ナ', 'ヌ'])
  assert.equal((await call('/atlas?purpose=review&limit=1')).categories.find(c => c.label === 'ヌ').grapheme, 'U+30CA', 'a corpus-only character is filed under its family')
  await call('/atlas?purpose=review&grapheme=U%2B30CC', undefined, 422)
  assert.deepEqual((await call('/atlas?purpose=review&grapheme=U%2B30CA&character=ヌ&state=pending')).items.map(i => i.id), [], 'a character narrows a grapheme to its own crops, none of them local')
  const nuShown = pairedRound.items.find(i => i.id === 'nu-shown')
  const memberRound = { id: crypto.randomUUID(), grapheme: 'U+30CA', answers: [{ id: 'nu-shown', revision: nuShown.revision,
    source_revision: nuShown.source_revision, verdict: 'wrong', issue: 'crop' }] }
  assert.equal((await call('/atlas/rounds', memberRound, 200, 'carol')).results.length, 1, 'a round answers any character of its grapheme')
  await call(`/atlas/rounds/${memberRound.id}/undo`, {}, 200, 'carol')
  await call('/atlas/rounds', { id: crypto.randomUUID(), grapheme: 'U+30C8', answers: [{ id: 'na-local-a', revision: 0,
    image_sha256: hash, verdict: 'wrong', issue: 'crop' }] }, 409, 'carol')
  await call('/atlas/rounds', { id: crypto.randomUUID(), label: 'ナ', seen: [{ id: 'na-local-a', image_sha256: hash }] }, 422, 'carol')
  await db.batch(paired.members.map(({ code_point }) => db.prepare('DELETE FROM characters WHERE code_point=?').bind(code_point)))
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
    ['["ナ"]', 'printed/type', 'printed/type0'], ONE_CHARACTER])
  // What a publication runs after it rewrites corpus_units, and what the trigger runs on each naming.
  shapes.push([{ sql: refresh[0], values: [] }, [], 'sqlite_autoindex_corpus_units_1'])
  shapes.push([{ sql: "UPDATE corpus_characters SET named=named+1 WHERE (character,production)=(SELECT character,production FROM corpus_units WHERE id=? AND named=0)", values: [] },
    ['na-1'], 'sqlite_autoindex_corpus_units_1'])
  // GET /atlas/history: all history newest first, by user, and by label, each served by its own partial index.
  // A user's page merges one index-ordered read per id they hold, so it stops at the LIMIT.
  const held = ['integration', 'reviewer-0000000a']
  const historyAll = worker.historyQuery(null, null, null)
  shapes.push([{ sql: historyAll.sql, values: [] }, [...historyAll.values, 41], 'event_history'])
  const byUser = worker.historyQuery(held, null, null)
  shapes.push([{ sql: byUser.sql, values: [] }, [...byUser.values, 41], 'event_actor_history'])
  const historyByLabel = worker.historyQuery(null, 'ア', null)
  shapes.push([{ sql: historyByLabel.sql, values: [] }, [...historyByLabel.values, 41], 'event_label_history'])
  // The keyset cursor stays on the same index once a page is under way, for the plain and the user shape.
  const cursor = { at: '2026-01-01T00:00:00.000Z', id: 'cf:0' }
  const allAfter = worker.historyQuery(null, null, cursor)
  shapes.push([{ sql: allAfter.sql, values: [] }, [...allAfter.values, 41], 'event_history'])
  const byUserAfter = worker.historyQuery(held, null, cursor)
  shapes.push([{ sql: byUserAfter.sql, values: [] }, [...byUserAfter.values, 41], 'event_actor_history'])
  for (const shape of [byUser, byUserAfter]) {
    const reads = (await plan({ sql: shape.sql, values: [] }, [...shape.values, 41])).filter(d => /^(SCAN|SEARCH) events\b/.test(d))
    assert.ok(reads.length === held.length && reads.every(d => /USING INDEX event_actor_history \(actor=\?/.test(d)), `one index read per id: ${reads.join('; ')}`)
    // Each id's passed rounds are read the same way, from its own partial index.
    const rounds = (await plan({ sql: shape.sql, values: [] }, [...shape.values, 41])).filter(d => /^(SCAN|SEARCH) submissions\b/.test(d))
    assert.ok(rounds.length === held.length && rounds.every(d => /USING INDEX submission_actor_history \(actor=\?/.test(d)), `one round read per id: ${rounds.join('; ')}`)
  }
  // Browsing one grapheme deals its crops from the seed's point in shuffle order, as `catalogue` asks.
  shapes.push([{ sql: "SELECT * FROM units WHERE origin='local' AND family=? AND shuffle>=? ORDER BY shuffle,rowid LIMIT ? OFFSET ?", values: [] },
    ['U+4EEE', 0, 60, 0], 'unit_family_sample'])
  // A round and its reference strips count their own character only, through its index; the review
  // filter off its index keeps the planner from walking every crop that can be dealt.
  const roundFilter = worker.listingFilter(true, 'not:printed/type', ['ナ'])
  shapes.push([{ sql: worker.facetsQueries(true, 'integration', roundFilter.where, true).stored, values: [] }, roundFilter.values, ONE_CHARACTER])
  // Every character's counts find a reviewer's skips during the rest by who and when, read each mark
  // once and look its crop up by id.
  const allCounts = worker.facetsQueries(true, 'integration', worker.listingFilter(true, 'not:printed/type', null).where, false)
  shapes.push([{ sql: allCounts.skipped, values: [] }, ['printed/type', 'printed/type0'], 'skip_actor'])
  const markPlan = await plan({ sql: allCounts.marked, values: [] }, ['printed/type', 'printed/type0'])
  assert.ok(markPlan.some(d => /^SCAN m\b/.test(d)) && markPlan.some(d => /^SEARCH units USING INDEX sqlite_autoindex_units_1 \(id=\?\)/.test(d)), markPlan.join('; '))
  shapes.push([worker.corpusCountQuery('not:printed/type', ['ナ']), [], null])
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
  // The derived tier stands apart: a character no source relates to 仮 and a form no character has,
  // each with the routes it came by and the pairs behind each substitution (a source's own statement
  // of two components among them), cited as derived-ids beside the pairs' sources, and never among the
  // variants a gallery widens to.
  await db.batch([
    db.prepare(`INSERT INTO component_variants VALUES('反','𠬝',3,'[{"a":"扳","b":"𢪃","sources":["wikidata"]},{"a":"返","b":"𮞉","sources":["cjkvi-variants","wikidata"]}]')`),
    db.prepare(`INSERT INTO component_variants VALUES('亻','彳',1,'[{"a":"亻","b":"彳","sources":["mkpoli-2026-10-04"]}]')`),
    db.prepare(`INSERT INTO character_derived VALUES('仮','[["𠈌",[[["反","𠬝"]]]],["⿰亻𠬝",[[["反","𠬝"]]]],["⿰彳𠬝",[[["亻","彳"],["反","𠬝"]]]]]')`),
    db.prepare(`INSERT INTO character_derived VALUES('𠈌','[["仮",[[["𠬝","反"]]]]]')`),
    db.prepare(`INSERT INTO character_derived VALUES('伋','[["⿰亻𠬝",[[["反","𠬝"]]]]]')`),
    db.prepare(`INSERT OR REPLACE INTO metadata VALUES('variant_sources','{"wikidata":"Wikidata, P5475; CC0-1.0","opencc":"OpenCC; Apache-2.0","cjkvi-variants":"CJKVI; PD","unihan":"Unihan; Unicode-3.0","derived-ids":"Predicted component variants (derived, not attested)","mkpoli-2026-10-04":"mkpoli, instruction of 2026-10-04"}')`),
    db.prepare("INSERT OR REPLACE INTO metadata VALUES('units_refreshed_at','\"variants-test-derived\"')"),
  ])
  const derivedCard = await call('/layers/characters/U%2B4EEE')
  assert.deepEqual(derivedCard.variants.derived.map(v => [v.char, v.code_point, v.encoded]),
    [['𠈌', 'U+2020C', true], ['⿰亻𠬝', null, false], ['⿰彳𠬝', null, false]], 'in rank order; another character\'s rows are its own')
  assert.deepEqual(derivedCard.variants.derived[0].routes.map(route => route.map(s => [s.was, s.became, s.count, s.pairs.length])), [[['反', '𠬝', 3, 2]]])
  assert.deepEqual(derivedCard.variants.derived[0].sources, ['cjkvi-variants', 'wikidata'])
  assert.deepEqual(derivedCard.variants.derived[2].routes[0].map(s => [s.was, s.became]), [['亻', '彳'], ['反', '𠬝']])
  assert.deepEqual(derivedCard.variants.derived[2].sources, ['cjkvi-variants', 'mkpoli-2026-10-04', 'wikidata'])
  assert.ok(!('tier' in derivedCard.variants.derived[2]), 'a derived form carries no tier')
  assert.equal(derivedCard.variants.sources['mkpoli-2026-10-04'], 'mkpoli, instruction of 2026-10-04')
  assert.equal(derivedCard.variants.sources['derived-ids'], 'Predicted component variants (derived, not attested)')
  assert.ok(!derivedCard.variants.items.some(v => v.char === '𠈌') && !derivedCard.variants.related.some(v => v.char === '𠈌'), 'a derived form is in no attested tier')
  // The IDS editor's start: 仮's descriptions by key, and the substitutes of each component, most
  // contexts first, read by key on either side of a substitution.
  await db.batch([db.prepare(`INSERT INTO han_ids VALUES('仮','["⿰亻反"]')`)])
  const structure = await call('/layers/structure?c=%E4%BB%AE')
  assert.deepEqual(structure.sequences, ['⿰亻反'])
  assert.deepEqual(structure.substitutes['反'], [{ char: '𠬝', count: 3 }])
  assert.deepEqual(structure.substitutes['亻'], [{ char: '彳', count: 1 }])
  assert.deepEqual((await call('/layers/structure?c=%E5%81%87')).sequences, [], 'a character with no row has no description')
  await call('/layers/structure?c=ab', null, 422)
  const structurePlan = await plan({ sql: worker.structureQuery(), values: [] }, ['仮'])
  assert.ok(structurePlan.some(d => /SEARCH han_ids USING PRIMARY KEY \(char=\?\)/.test(d)), structurePlan.join('; '))
  const substitutesPlan = await plan({ sql: worker.substitutesQuery(2), values: [] }, ['亻', '反', '亻', '反'])
  assert.ok(substitutesPlan.some(d => /SEARCH component_variants USING PRIMARY KEY \(a=\?\)/.test(d)), substitutesPlan.join('; '))
  assert.ok(substitutesPlan.some(d => /SEARCH component_variants USING (COVERING )?INDEX component_variant_b \(b=\?\)/.test(d)), substitutesPlan.join('; '))
  assert.ok(!substitutesPlan.some(d => /^SCAN/.test(d)), substitutesPlan.join('; '))
  const derivedPlan = await plan({ sql: worker.derivedEdgesQuery(), values: [] }, ['仮'])
  assert.ok(derivedPlan.includes('SEARCH character_derived USING PRIMARY KEY (a=?)'), derivedPlan.join('; '))
  assert.ok(!derivedPlan.some(d => /TEMP B-TREE/.test(d)), derivedPlan.join('; '))
  // A card lists the words a character is cited as writing (0064), each with every spelling cited for
  // it, most cited first; the other spellings stay out of the variant tiers.
  await db.batch([
    db.prepare("INSERT INTO words VALUES('ja/ばかり/副助詞','ja','ばかり','副助詞')"),
    db.prepare("INSERT INTO word_spellings VALUES('ja/ばかり/副助詞','仮','honkoku-ruby','ruby-spellings.tsv ばかり 仮','observed','reading','','','仮（ばかり）',2,3)"),
    db.prepare("INSERT INTO word_spellings VALUES('ja/ばかり/副助詞','計','honkoku-ruby','ruby-spellings.tsv ばかり 計','observed','reading','','','計（ばかり）',48,114)"),
    db.prepare("INSERT INTO word_spellings VALUES('ja/ばかり/副助詞','計','wiktionary-ja','ばかり, revision 2291631','attested','source','','','【計り】',NULL,NULL)"),
    db.prepare("INSERT INTO word_spellings VALUES('ja/ばかり/副助詞','而已','honkoku-ruby','ruby-spellings.tsv ばかり 而已','observed','reading','','','而已（ばかり）',2,3)"),
    db.prepare(`INSERT OR REPLACE INTO metadata VALUES('word_sources','{"honkoku-ruby":"みんなで翻刻 振り仮名","wiktionary-ja":"Wiktionary 日本語版"}')`),
    db.prepare("INSERT OR REPLACE INTO metadata VALUES('units_refreshed_at','\"words-test\"')"),
  ])
  const wordCard = await call('/layers/characters/U%2B4EEE')
  assert.deepEqual(wordCard.words.items.map(w => [w.id, w.spellings.map(s => [s.spelling, s.code_point, s.current])]),
    [['ja/ばかり/副助詞', [['計', 'U+8A08', false], ['仮', 'U+4EEE', true], ['而已', null, false]]]])
  assert.deepEqual(wordCard.words.items[0].spellings[0].sources.map(s => [s.source, s.tier, s.ruby, s.documents]),
    [['honkoku-ruby', 'observed', 'ばかり', 48], ['wiktionary-ja', 'attested', null, null]], 'a 振り仮名 row names the reading it counts')
  assert.equal(wordCard.words.sources['wiktionary-ja'], 'Wiktionary 日本語版')
  assert.ok(!wordCard.variants.items.concat(wordCard.variants.related).some(v => v.char === '計'), 'a shared word is no variant')
  assert.deepEqual((await call('/layers/characters/U%2B5047')).words.items, [], 'a character no source cites writes no word')
  const wordPlan = await plan({ sql: worker.wordSpellingsQuery(), values: [] }, ['仮'])
  assert.ok(wordPlan.some(d => /word_spelling_spelling \(spelling=\?\)/.test(d)), wordPlan.join('; '))
  assert.ok(!wordPlan.some(d => /^SCAN/.test(d)), wordPlan.join('; '))
  // A gallery widened to its variants deals a variant's crops with the character's own, and only then.
  const variantCrop = { id: 'variant-crop', label: '假', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('variant-crop', 'local', '假', 'U+4EEE', null,
    'handwritten', 'kanji', 'pending', 0, 1, 1, 1, JSON.stringify(variantCrop), JSON.stringify({ character: variantCrop }), '{}', '{}', null).run()
  assert.ok(!(await call('/layers/occurrences?code_point=U%2B4EEE')).items.some(i => i.id === 'variant-crop'), 'the exact character alone')
  assert.ok((await call('/layers/occurrences?code_point=U%2B4EEE&expand=variants')).items.some(i => i.id === 'variant-crop'), 'widened to 假')
  const derivedCrop = { ...variantCrop, id: 'derived-crop', label: '𠈌', reading: '𠈌' }
  await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('derived-crop', 'local', '𠈌', '𠈌', 'U+2020C', null,
    'handwritten', 'kanji', 'pending', 0, 1, 1, 1, JSON.stringify(derivedCrop), JSON.stringify({ character: derivedCrop }), '{}', '{}', null).run()
  assert.ok(!(await call('/layers/occurrences?code_point=U%2B4EEE&expand=variants')).items.some(i => i.id === 'derived-crop'), 'a derived form is not widened to')
  // A crop of 伋 is not dealt: a source calls the pair a simplification, so it is kept apart.
  const apart = { ...variantCrop, id: 'apart-crop', label: '伋' }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('apart-crop', 'local', '伋', 'U+4EEE', null,
    'handwritten', 'kanji', 'pending', 0, 1, 1, 1, JSON.stringify(apart), JSON.stringify({ character: apart }), '{}', '{}', null).run()
  assert.ok(!(await call('/layers/occurrences?code_point=U%2B4EEE&expand=variants')).items.some(i => i.id === 'apart-crop'), 'a simplified pair is not widened to')
  assert.equal((await call('/layers/candidates?code_point=U%2B4EEE&scope=variants')).retry, false, 'the corpus side widens without error')
  await call('/layers/occurrences?code_point=U%2B4EEE&expand=variants&offset=2001', undefined, 404)
  // A crop's book's dates (0052) travel with it in a listing and, with the claims behind them, in its inspector.
  const datedCrop = { ...variantCrop, id: 'dated-crop', label: '仮', source: '某書' }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('dated-crop', 'local', '仮', 'U+4EEE', null,
    'handwritten', 'kanji', 'pending', 0, 1, 1, 1, JSON.stringify(datedCrop), JSON.stringify({ character: datedCrop }), '{}', '{}', 'doc:dated').run()
  const conversion = JSON.stringify({ service: 'hutime', query: 'https://ap.hutime.org/cal/?method=conv&ival=寛政3年' })
  const dateValue = JSON.stringify({ of: 'witness', text: '寛政三年', start: 1791, end: 1791, precision: 'year', calendar: 'japanese', conversion: JSON.parse(conversion) })
  await db.batch([
    db.prepare(`INSERT INTO document_dating(document,axis,kind,start,end,precision,qualifier,uncertain,text,label,status,claims,calendar,conversion,source,resolver,export)
      VALUES('doc:dated','witness','copied',1791,1791,'year',NULL,0,'寛政三年','1791','single','["dt:1"]','japanese',?,'kokusho','dates-1','x')`).bind(conversion),
    db.prepare(`INSERT INTO assertions(id,subject,predicate,scope,slot,value,tier,asserted_by,asserted_at) VALUES('dt:1','doc:dated','date_copied','',?,?,'attested','source:kokusho','x')`).bind(dateValue, dateValue),
    db.prepare("INSERT INTO assertion_evidence(assertion,kind,ref,locator) VALUES('dt:1','source','kokusho','https://kokusho.nijl.ac.jp/biblio/1#bpublish.0')"),
  ])
  const datedItem = (await call('/layers/occurrences?code_point=U%2B4EEE')).items.find(i => i.id === 'dated-crop')
  assert.deepEqual([datedItem.dating.witness.kind, datedItem.dating.witness.label, datedItem.dating.witness.hutime], ['copied', '1791', JSON.parse(conversion).query])
  assert.ok((await call('/layers/occurrences?code_point=U%2B4EEE')).items.filter(i => i.id !== 'dated-crop').every(i => i.dating && !Object.keys(i.dating).length), 'an undated book gives an empty dating')
  const inspectedDates = (await call('/atlas/characters/dated-crop')).dates
  assert.deepEqual(inspectedDates.map(d => [d.kind, d.text, d.source, d.locator]), [['copied', '寛政三年', 'kokusho', 'https://kokusho.nijl.ac.jp/biblio/1#bpublish.0']])
  // A gallery placed by date lists the dated crop first and the undated after; a range keeps the dated
  // one, `undated` the rest; the decades count both.
  const undatedCrop = { ...datedCrop, id: 'undated-crop' }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('undated-crop', 'local', '仮', 'U+4EEE', null,
    'handwritten', 'kanji', 'pending', 0, 1, 1, 0, JSON.stringify(undatedCrop), JSON.stringify({ character: undatedCrop }), '{}', '{}', 'doc:undated').run()
  assert.deepEqual((await call('/layers/occurrences?code_point=U%2B4EEE')).items.find(i => i.id === 'undated-crop').dating, {})
  const byYear = (await call('/layers/occurrences?code_point=U%2B4EEE&order=year&limit=200')).items.map(i => i.id)
  assert.deepEqual([byYear[0], byYear.at(-1)], ['dated-crop', 'undated-crop'], 'oldest first, undated last')
  assert.deepEqual((await call('/layers/occurrences?code_point=U%2B4EEE&years=undated')).items.map(i => i.id), ['undated-crop'])
  assert.deepEqual((await call('/layers/occurrences?code_point=U%2B4EEE&years=1700-1800')).items.map(i => i.id), ['dated-crop'])
  assert.ok(!(await call('/layers/occurrences?code_point=U%2B4EEE&years=undated&limit=200')).items.some(i => i.id === 'dated-crop'))
  assert.equal((await call('/layers/occurrences?code_point=U%2B4EEE&years=1800-1900')).total, 0)
  await call('/layers/occurrences?code_point=U%2B4EEE&years=1900-1800', undefined, 422)
  const decadeCounts = await call('/layers/decades?code_point=U%2B4EEE')
  assert.ok(decadeCounts.local.some(([decade, n]) => decade === 1790 && n === 1), JSON.stringify(decadeCounts))
  assert.ok(decadeCounts.local.some(([decade]) => decade === null), 'the undated are counted')
  assert.ok(Array.isArray((await call('/layers/candidates?code_point=U%2B4EEE&order=year')).glyph_items))
  // The time axis (編年): each decade's crops counted and a sample drawn, the undated apart, and the
  // filters on style, production and characters.
  const axis = await call('/layers/chronology?code_point=U%2B4EEE')
  assert.deepEqual(axis.buckets.map(b => [b.decade, b.local, b.items.map(i => i.id)]), [[1790, 1, ['dated-crop']]])
  assert.equal(axis.buckets[0].items[0].dating.witness.label, '1791')
  assert.ok(axis.undated.local >= 1 && axis.undated.items.some(i => i.id === 'undated-crop'))
  assert.equal((await call('/layers/chronology?code_point=U%2B4EEE&production=printed')).buckets.length, 0)
  assert.equal((await call('/layers/chronology?code_point=U%2B4EEE&production=handwritten&chars=%E4%BB%AE')).buckets.length, 1)
  await call('/layers/chronology?code_point=U%2B4EEE&production=Robert%27);', undefined, 422)
  for (const [sql, values] of [[worker.localChronologyQuery(false, 'witness'), ['local', '仮', 6]], [worker.corpusChronologyQuery(false, 'witness'), ['仮', '仮', 6]]]) {
    const details = await plan({ sql, values: [] }, values)
    assert.ok(!details.some(d => /^SCAN (units|corpus_units|u|c|d)\b/.test(d)), details.join('; '))
  }
  const datedFrom = "FROM units u LEFT JOIN document_dating d ON d.document=u.document AND d.axis='witness' WHERE u.origin=? AND u.character=?"
  for (const [sql, values] of [[worker.localDecadesQuery(false, 'witness'), ['local', '仮']], [worker.localDecadesQuery(true, 'witness'), ['local', 'U+4EEE', 'local', '仮']],
    [worker.corpusDecadesQuery(false, 'witness'), ['仮', '仮']], [worker.datedCropsQuery(datedFrom, '', 'year'), ['local', '仮', 60, 0]]]) {
    const details = await plan({ sql, values: [] }, values)
    assert.ok(!details.some(d => /^SCAN (units|corpus_units|u|c|d)\b/.test(d)), details.join('; '))
  }
  // The collection's dates in numbers, from the cached counts: the dated crop's book in its hundred
  // years, decade and kind, a corpus book with no date among the undated works.
  await db.prepare("INSERT INTO corpus_document_counts(document,n) VALUES('doc:corpus-undated',4)").run()
  const dateNumbers = await call('/atlas/dates/stats')
  assert.ok(dateNumbers.hundreds.some(([start, crops, works]) => start === 1700 && crops === 1 && works === 1), JSON.stringify(dateNumbers))
  assert.ok(dateNumbers.decades.some(([start]) => start === 1790))
  assert.deepEqual(dateNumbers.kinds.find(([kind]) => kind === 'copied'), ['copied', 1, 1])
  assert.ok(dateNumbers.total.works - dateNumbers.dated.works >= 1 && dateNumbers.total.crops >= dateNumbers.dated.crops + 4)
  const statsPlan = await plan({ sql: worker.dateStatsQuery(), values: [] }, [])
  assert.ok(!statsPlan.some(d => /^SCAN (units|corpus_units)\b/.test(d)), statsPlan.join('; '))
  for (const [sql, values] of [[worker.datingQuery(2), ['a', 'b']], [worker.dateClaimsQuery(), ['doc:dated']]]) {
    const details = await plan({ sql, values: [] }, values)
    assert.ok(!details.some(d => /^SCAN (document_dating|assertions|a)\b/.test(d)), details.join('; '))
  }
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
  // Pair and trigram frequencies, the whole site's and a book's: the first page of the counts the
  // triggers keep, read in rank order with no sort.
  const ngramShapes = [[false, ['', 2], 'ngram_count_rank'], [true, ['hk:doc', 2], 'ngram_count_rank']]
  const ngramServed = (details, index) => {
    assert.ok(details.some(d => new RegExp(`SEARCH ngram_counts USING COVERING INDEX ${index} \\(scope=\\? AND size=\\?\\)`).test(d)), `${index}: ${details.join('; ')}`)
    assert.ok(!details.some(d => d.includes('TEMP B-TREE')), details.join('; '))
    assert.ok(!details.some(d => /^SCAN \w+/.test(d)), details.join('; '))
  }
  for (const [, bound, index] of ngramShapes) {
    const shape = { sql: worker.ngramsQuery(), values: [] }
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
  assert.deepEqual((await db.prepare("SELECT text,n FROM ngram_counts WHERE scope='' AND size=2 AND text IN (?,?) ORDER BY text").bind(firstLabel + secondLabel, firstLabel + 'ヰ').all()).results,
    [{ text: firstLabel + 'ヰ', n: 1 }], 'the counts follow a relabelled run')
  await db.prepare("UPDATE units SET character=? WHERE id='two'").bind(secondLabel).run()
  // One run's occurrences: read along the first row's index in its key order, every other row and each
  // crop by its key, never sorted or scanned, for every length and every row a long run can start from;
  // a book's through the index it shares with the count.
  const occurrenceServed = (details, index) => {
    assert.ok(details.some(d => new RegExp(`SEARCH a USING (COVERING )?INDEX ${index}\\b`).test(d)), `${index}: ${details.join('; ')}`)
    assert.ok(!details.some(d => d.includes('TEMP B-TREE')), details.join('; '))
    assert.ok(!details.some(d => /^SCAN \w+/.test(d)), details.join('; '))
    assert.ok(details.filter(d => /^SEARCH u\d+ /.test(d)).every(d => /USING INDEX sqlite_autoindex_units_1 \(id=\?\)( LEFT-JOIN)?$/.test(d)), `crops are found by their ids: ${details.join('; ')}`)
    assert.ok(details.filter(d => /^SEARCH k\d+ /.test(d)).every(d => /USING (COVERING )?INDEX sqlite_autoindex_corpus_units_1 \(id=\?\)( LEFT-JOIN)?$/.test(d)), `corpus glyphs are found by their ids: ${details.join('; ')}`)
    assert.ok(details.filter(d => /^SEARCH r\d+ /.test(d)).every(d => d.includes('USING PRIMARY KEY (first=? AND size=?)')), `rightward pairs are found by their keys: ${details.join('; ')}`)
    assert.ok(details.filter(d => /^SEARCH l\d+ /.test(d)).every(d => d.includes('unit_ngram_second')), `leftward pairs go through unit_ngram_second: ${details.join('; ')}`)
  }
  for (const [document, scope, index] of [[false, [], 'unit_ngram_graphemes'], [true, ['hk:doc'], 'unit_ngram_graphemes_work']]) {
    const probe = await plan({ sql: worker.runProbeQuery(document), values: [] }, [...scope, 'ナリケ'])
    // A book's probe is served as well by the index that places a run by book (0059), which leads with the same terms.
    assert.ok(probe.some(d => new RegExp(`USING COVERING INDEX (${index}|${document ? 'unit_ngram_graphemes_source' : index})\\b`).test(d)), probe.join('; '))
    for (let size = 2; size <= 8; size++) for (let anchor = 0; anchor <= Math.max(0, size - 3); anchor++) for (const style of [false, true]) {
      const links = worker.runFrom(size, anchor).links.map(() => 'ナリ'), bound = [...links, ...scope, Math.min(size, 3), 'ナリ', ...(style ? [0] : [])]
      for (const shape of [{ sql: worker.runOccurrencesQuery(size, anchor, document, style), values: [] }, { sql: worker.runCountQuery(size, anchor, document, style), values: [] }]) {
        const args = shape.sql.includes('OFFSET') ? [...bound, 48, 0] : bound
        // A book's count asks for no order, and the index that places a run by book (0059) serves it as well.
        occurrenceServed(await plan(shape, args), document && !shape.sql.includes('OFFSET') ? `(?:${index}|unit_ngram_graphemes_source)` : index)
        if (size !== 5 || anchor !== 1 || style) continue
        const create = (await db.prepare('SELECT sql FROM sqlite_master WHERE name=?').bind(index).first()).sql
        await db.prepare(`DROP INDEX ${index}`).run()
        await assert.rejects(async () => occurrenceServed(await plan({ ...shape, sql: shape.sql + ' ' }, args), index), `the occurrence check on ${index} fails without it`)
        await db.prepare(create).run()
      }
    }
  }
  // Placed by book, a run reads the index that keeps its rows in book order, whole or in a group.
  for (let size = 2; size <= 8; size++) for (let anchor = 0; anchor <= Math.max(0, size - 3); anchor++) for (const style of [false, true]) {
    const links = worker.runFrom(size, anchor).links.map(() => 'ナリ'), bound = [...links, Math.min(size, 3), 'ナリ', ...(style ? [0] : []), 48, 0]
    const shape = { sql: worker.runOccurrencesQuery(size, anchor, false, style, 'source'), values: [] }
    occurrenceServed(await plan(shape, bound), 'unit_ngram_graphemes_source')
    if (size !== 5 || anchor !== 1 || style) continue
    const create = (await db.prepare('SELECT sql FROM sqlite_master WHERE name=?').bind('unit_ngram_graphemes_source').first()).sql
    await db.prepare('DROP INDEX unit_ngram_graphemes_source').run()
    await assert.rejects(async () => occurrenceServed(await plan({ ...shape, sql: shape.sql + ' ' }, bound), 'unit_ngram_graphemes_source'), 'the check on unit_ngram_graphemes_source fails without it')
    await db.prepare(create).run()
  }
  // The books of a run are grouped from a capped read of it, and the runs near it from one range of the index.
  const nearServed = (details, index) => {
    assert.ok(details.some(d => new RegExp(`SEARCH \\w+ USING (COVERING )?INDEX ${index}\\b`).test(d)), `${index}: ${details.join('; ')}`)
    assert.ok(!details.some(d => /^SCAN \w+ ?$/.test(d) || /^SCAN [a-z]\d? *$/.test(d)), details.join('; '))
  }
  for (const [sql, bound, index] of [[worker.runWorksQuery(2, 0), [2, 'ナリ'], 'unit_ngram_graphemes_source'], [worker.runWorksQuery(5, 1), ['ナリ', 'ナリ', 3, 'ナリ'], 'unit_ngram_graphemes_source'],
  ]) {
    nearServed(await plan({ sql, values: [] }, bound), index)
    const create = (await db.prepare('SELECT sql FROM sqlite_master WHERE name=?').bind(index).first()).sql
    await db.prepare(`DROP INDEX ${index}`).run()
    await assert.rejects(async () => nearServed(await plan({ sql: sql + ' ', values: [] }, bound), index), `the check on ${index} fails without it`)
    await db.prepare(create).run()
  }
  // The runs near one read the site's counts by their key: a prefix is one key range, a text one key.
  for (const [sql, bound, key] of [[worker.runRangeQuery(), [3, 'ナリ', 'ナリ\u{10FFFF}', 'ナリ'], /^SEARCH ngram_counts USING PRIMARY KEY \(scope=\? AND size=\? AND text>\? AND text<\?\)$/],
    [worker.runHasQuery(), [2, 'ナリ'], /^SEARCH ngram_counts USING PRIMARY KEY \(scope=\? AND size=\? AND text=\?\)$/]]) {
    const details = await plan({ sql, values: [] }, bound)
    assert.ok(details.some(d => key.test(d)) && !details.some(d => /^SCAN /.test(d)), details.join('; '))
  }
  const leftward = (await db.prepare("SELECT sql FROM sqlite_master WHERE name='unit_ngram_second'").first()).sql
  await db.prepare('DROP INDEX unit_ngram_second').run()
  await assert.rejects(async () => occurrenceServed(await plan({ sql: worker.runOccurrencesQuery(5, 2, false) + ' ', values: [] }, ['ナリ', 'ナリ', 3, 'ナリ', 48, 0]), 'unit_ngram_graphemes'),
    'the leftward check fails without unit_ngram_second')
  await db.prepare(leftward).run()
  const runOf = async (text, query = '') => (await mf.dispatchFetch(base + '/atlas/runs?' + new URLSearchParams({ text }) + query)).json()
  const pairText = firstLabel + secondLabel
  const occurrences = await runOf(pairText)
  assert.equal(occurrences.total, 1, 'a pair counts the occurrences whose crops are both live')
  assert.deepEqual(occurrences.items.map(o => o.crops.map(c => c.id)), [['one', 'two']], 'an occurrence carries its crops in reading order')
  assert.ok(!('context_image' in occurrences.items[0].crops[0]), 'occurrences carry listing fields only')
  // The page around a run is the smallest context render that holds all its crops, clipped to them with
  // a margin of a fifth of the largest crop that stays inside the render.
  const placed = (x, y, context) => ({ crop_box: { x, y, w: 10, h: 10 }, context_image: `/atlas/media/${x}-${y}.webp`, context_box: context })
  assert.deepEqual(worker.runPage([placed(50, 50, { x: 0, y: 0, w: 200, h: 200 }), placed(50, 62, { x: 20, y: 20, w: 100, h: 100 })]),
    { image: '/atlas/media/50-62.webp', box: { x: 20, y: 20, w: 100, h: 100 }, region: { x: 48, y: 48, w: 14, h: 26 } })
  assert.deepEqual(worker.runPage([placed(21, 21, { x: 20, y: 20, w: 100, h: 100 }), placed(21, 33, { x: 20, y: 20, w: 100, h: 100 })]).region,
    { x: 20, y: 20, w: 13, h: 25 }, 'the margin stays inside the render')
  assert.equal(worker.runPage([placed(50, 50, { x: 45, y: 45, w: 20, h: 20 }), placed(50, 70, { x: 45, y: 65, w: 20, h: 20 })]), null, 'no render holds both crops')
  assert.equal(worker.runPage([placed(50, 50, { x: 0, y: 0, w: 200, h: 200 }), { crop_box: null }]), null, 'a crop without a box has no page')
  assert.ok('page' in occurrences.items[0] && 'crop_box' in occurrences.items[0].crops[0], 'an occurrence carries its page and its crops\' boxes')
  assert.deepEqual([occurrences.vertical, occurrences.items[0].vertical], [true, true], 'a run is written the way its line is')
  // A trigram is shown only while its third crop is live as well.
  await db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document) VALUES('one',3,'two','gone',?,NULL)`).bind(pairText + '也').run()
  // A run with a member the site does not hold keeps its text as its graphemes (0071); this one is given
  // the graphemes its query folds to, so only the dead third crop keeps it off the page.
  await db.prepare("UPDATE unit_ngrams SET graphemes=(SELECT graphemes FROM unit_ngrams WHERE first='one' AND size=2)||'也' WHERE first='one' AND size=3").run()
  assert.equal((await runOf(pairText + '也')).total, 0, 'a trigram needs its third crop live')
  await db.prepare("UPDATE unit_ngrams SET third='one',text=? WHERE first='one' AND size=3").bind(pairText + firstLabel).run()
  const trigram = await runOf(pairText + firstLabel)
  assert.deepEqual([trigram.total, trigram.items.map(o => o.crops.map(c => c.id))], [1, [['one', 'two', 'one']]], 'a trigram carries its three crops')
  // A longer run chains pairs onto its rarest trigram, leftwards and rightwards: one→two→one→two→one
  // reads its trigrams at every start, and its pairs where they join.
  await db.batch([
    db.prepare(`INSERT OR REPLACE INTO unit_ngrams(first,size,second,third,text,document) VALUES('two',2,'one',NULL,?,NULL),('two',3,'one','two',?,NULL)`).bind(secondLabel + firstLabel, secondLabel + pairText),
  ])
  const four = await runOf(pairText + pairText)
  assert.deepEqual([four.size, four.total, four.items.map(o => o.crops.map(c => c.id))], [4, 1, [['one', 'two', 'one', 'two']]], 'a run of four chains a pair onto a trigram')
  const five = await runOf(pairText + pairText + firstLabel)
  assert.deepEqual(five.items.map(o => o.crops.map(c => c.id)), [['one', 'two', 'one', 'two', 'one']], 'a run of five')
  assert.equal((await runOf(pairText + secondLabel + secondLabel)).total, 0, 'a link whose pair is missing breaks the run')
  // Rows of one→two→one whose crops are not live make it the commoner trigram, so the runs start from
  // two→one→two inside them and reach their first crop leftwards. A refresh moves the catalogue version
  // past the answers the edge keeps.
  await db.batch([db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document) VALUES('ghost1',3,'ghost2','ghost3',?1,NULL),('ghost2',3,'ghost3','ghost4',?1,NULL)`).bind(pairText + firstLabel),
    db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at','leftward')")])
  await db.prepare("UPDATE unit_ngrams SET graphemes=(SELECT graphemes FROM unit_ngrams WHERE first='one' AND size=3) WHERE first LIKE 'ghost%'").run()
  const inner = await runOf(pairText + pairText)
  assert.deepEqual(inner.items.map(o => o.crops.map(c => c.id)), [['one', 'two', 'one', 'two']], 'a run reached leftwards keeps its reading order')
  assert.deepEqual((await runOf(pairText + pairText + firstLabel)).items.map(o => o.crops.map(c => c.id)), [['one', 'two', 'one', 'two', 'one']],
    'a run reached both ways from an inner trigram')
  assert.equal((await runOf(firstLabel + firstLabel + secondLabel + firstLabel)).total, 0, 'a missing link on the left breaks the run')
  await db.prepare("DELETE FROM unit_ngrams WHERE first LIKE 'ghost%'").run()
  assert.equal((await runOf(pairText + pairText, '&document=hk%3Aother')).total, 0, 'a book holds only its own runs')
  assert.equal((await runOf('申候')).total, 0, 'pairs whose crops are not live are not shown')
  assert.equal((await mf.dispatchFetch(base + '/atlas/runs?text=' + encodeURIComponent(firstLabel))).status, 422, 'a run is two characters or more')
  assert.equal((await mf.dispatchFetch(base + '/atlas/runs?text=' + encodeURIComponent('一二三四五六七八九'))).status, 422, 'a run is eight characters or fewer')
  assert.equal((await mf.dispatchFetch(base + '/atlas/runs?text=' + encodeURIComponent(pairText) + '&offset=2000')).status, 404, 'a run does not page past its cap')
  assert.equal((await mf.dispatchFetch(base + '/atlas/runs?text=' + encodeURIComponent(pairText) + '&limit=97')).status, 422, 'a page is bounded')
  assert.equal((await mf.dispatchFetch(base + '/atlas/runs?text=' + encodeURIComponent(pairText + pairText) + '&limit=49')).status, 422, 'a longer run pages fewer occurrences')
  const nextPage = await runOf(pairText, '&offset=1')
  assert.ok(!('total' in nextPage) && nextPage.next_offset === 1, 'a later page carries no count')
  // A run's occurrences come shaped by hand first (0062), then what nobody has classified, then type, each
  // group in its first crops' shuffle order; the group follows the crop's production and style, however
  // the run was written, and type comes last whatever the style.
  for (const [id, production, style, shuffle] of [['o-print', 'printed/type', 'running', 1], ['o-plain', 'unknown', 'unassessed', 5],
    ['o-late', 'unknown', 'cursive', 9], ['o-early', 'printed/woodblock', 'unassessed', 2]]) {
    const d = { id, label: 'ナ', state: 'pending', revision: 0, image_sha256: hash, production, repair: { quiz: true },
      source: ['o-early', 'o-late'].includes(id) ? 'Book A' : 'Book B' }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ナ', 'U+30CA', null, production, 'kana', 'pending', 0, 1, 1, shuffle,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
    await db.prepare('UPDATE units SET style=? WHERE id=?').bind(style, id).run()
  }
  await db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document) SELECT id,2,'two',NULL,'ナリ',CASE WHEN id IN ('o-early','o-late') THEN 'hk:a' ELSE 'hk:b' END FROM units WHERE id LIKE 'o-%'`).run()
  // These runs are written with a text their members do not spell; their graphemes are set from that text,
  // as if the members spelled it (0071 folds a run from its members; the graphemes test below covers that).
  const readAsWritten = () => db.prepare('UPDATE unit_ngrams SET graphemes=text WHERE graphemes IS NOT text').run()
  await readAsWritten()
  // The edge keeps an answer per catalogue version, so a change moves the version first.
  let version = 0
  const firsts = async (query = '') => { await db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',?)").bind('order' + ++version).run()
    return (await runOf('ナリ', query)).items.map(o => o.crops[0].id) }
  assert.deepEqual(await firsts(), ['o-early', 'o-late', 'o-plain', 'o-print'], 'woodblock and cursive first, then unclassified, then type, each by shuffle')
  assert.deepEqual(await firsts('&limit=2&offset=2'), ['o-plain', 'o-print'], 'a later page continues the same order')
  // A publication that rewrites a crop's production in place moves its runs.
  await db.prepare("UPDATE units SET production='printed/type/wood' WHERE id='o-early'").run()
  assert.deepEqual(await firsts(), ['o-late', 'o-plain', 'o-print', 'o-early'], 'a crop found to be type afterwards moves its runs')
  await db.prepare("UPDATE units SET production='handwritten',shuffle=0 WHERE id='o-print'").run()
  assert.deepEqual(await firsts(), ['o-print', 'o-late', 'o-plain', 'o-early'], 'and its shuffle moves them too')
  // A page narrows to a group or a book, and is placed by book on request; the first page says what each holds.
  await db.prepare("UPDATE units SET production='printed/type',shuffle=1 WHERE id='o-print'").run()
  await db.prepare("UPDATE units SET production='printed/woodblock',shuffle=2 WHERE id='o-early'").run()
  const narrowed = async (query = '') => { await db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',?)").bind('narrow' + ++version).run()
    return runOf('ナリ', query) }
  const firstsOf = body => body.items.map(o => o.crops[0].id)
  const whole = await narrowed()
  assert.deepEqual(whole.hands, { hand: 2, unknown: 1, type: 1 }, 'a run counts its crops by group')
  assert.deepEqual(whole.works, [{ id: 'hk:a', title: 'Book A', count: 2 }, { id: 'hk:b', title: 'Book B', count: 2 }], 'and its books, named by their crops')
  assert.deepEqual([whole.hand, whole.sort, whole.total, whole.hand_groups], ['all', 'hand', 4, ['hand', 'unknown', 'type']])
  assert.equal((await narrowed('&offset=2')).works, undefined, 'a later page does not ask for the books again')
  assert.deepEqual(firstsOf(await narrowed('&hand=hand')), ['o-early', 'o-late'], 'a group narrows the run')
  const typed = await narrowed('&hand=type')
  assert.deepEqual([typed.total, firstsOf(typed), typed.hands.hand], [1, ['o-print'], 2], 'a group counts only its own, and the groups still say what they hold')
  assert.deepEqual(firstsOf(await narrowed('&document=hk%3Ab')), ['o-plain', 'o-print'], 'a book narrows the run')
  assert.deepEqual((await narrowed('&document=hk%3Ab')).hands, { hand: 0, unknown: 1, type: 1 }, 'its groups are the book\'s')
  assert.deepEqual(firstsOf(await narrowed('&document=hk%3Aa&hand=hand')), ['o-early', 'o-late'], 'a book and a group together')
  assert.deepEqual(firstsOf(await narrowed('&sort=source')), ['o-early', 'o-late', 'o-plain', 'o-print'], 'by book, then by crop')
  assert.deepEqual(firstsOf(await narrowed('&sort=source&hand=hand')), ['o-early', 'o-late'])
  assert.equal((await mf.dispatchFetch(base + '/atlas/runs?text=' + encodeURIComponent('ナリ') + '&hand=bold')).status, 422, 'only the groups narrow')
  assert.equal((await mf.dispatchFetch(base + '/atlas/runs?text=' + encodeURIComponent('ナリ') + '&sort=style')).status, 422, 'only the group and the book place a run')
  // Near runs: the shorter runs inside a trigram, the trigrams a pair begins, and the runs that begin alike.
  await db.batch([db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document) VALUES('o-print',3,'two','o-early','ナリア',NULL),
    ('o-late',3,'two','o-early','ナリア',NULL),('o-plain',3,'two','o-early','ナリイ',NULL),('x-sibling',2,'one',NULL,'ナヌ',NULL)`)])
  const near = async text => (await (await mf.dispatchFetch(base + '/atlas/runs/related?' + new URLSearchParams({ text }))).json())
  const pair = await near('ナリ')
  assert.deepEqual(pair.longer, [{ text: 'ナリア', n: 2, vertical: true }, { text: 'ナリイ', n: 1, vertical: true }], 'a pair offers the trigrams it begins, most frequent first')
  assert.deepEqual([pair.siblings.map(r => r.text), pair.inside, pair.lead], [['ナヌ'], [], 'ナ'], 'and the pairs that share its first character')
  const triple = await near('ナリア')
  assert.deepEqual([triple.inside, triple.siblings.map(r => r.text), triple.longer, triple.lead], [['ナリ'], ['ナリイ'], [], 'ナリ'], 'a trigram offers the pairs it holds that the site has, and the trigrams that share its first two')
  assert.deepEqual((await near('ナリアイ')).inside, ['ナリア'], 'a run of four offers the trigrams inside it that the site has')
  assert.equal((await mf.dispatchFetch(base + '/atlas/runs/related?text=' + encodeURIComponent('ナ'))).status, 422)
  // A corpus glyph's runs are counted and listed with the crops'. Its record is read from its pack until a
  // round names it, and a run's text follows the character the site shows for each of its glyphs: the
  // one a decision gives its published row, then the one its `units` row carries once a round names it.
  {
    let pack = ''
    for (const [id, character, y] of [['hl:run:0', '申', 0], ['hl:run:1', '上', 12], ['hl:run:2', '候', 24]]) {
      const raw = JSON.stringify({ id, origin: 'corpus', label: character, written_character: character, proxyable: true, state: 'pending', revision: 0,
        image: `/atlas/media/${id}.webp`, crop_box: { x: 10, y, w: 10, h: 10 }, source: { corpus: 'honkoku-lines', title: 'A corpus book' } })
      await db.prepare(`INSERT INTO corpus_units(${CORPUS_COLUMNS},document) VALUES(?,?,?,?,?,?,?,?,?,?,?)`).bind(id, character, null, null, 5, 'pack-runs',
        new TextEncoder().encode(pack).length, new TextEncoder().encode(raw).length, 'unknown', 0, 'hl:book').run()
      pack += raw
    }
    await bucket.put('pack-runs', pack)
    const textOf = async (first, size = 2) => (await db.prepare('SELECT text FROM unit_ngrams WHERE first=? AND size=?').bind(first, size).first())?.text ?? null
    const countOf = async (text, size = 2) => (await db.prepare("SELECT n FROM ngram_counts WHERE scope='' AND size=? AND text=?").bind(size, text).first())?.n ?? 0
    const refreshed = stamp => db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',?)").bind(stamp).run()
    await db.batch([db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document) VALUES('hl:run:0',2,'hl:run:1',NULL,'申上','hl:book'),
      ('hl:run:1',2,'hl:run:2',NULL,'上候','hl:book'),('hl:run:0',3,'hl:run:1','hl:run:2','申上候','hl:book')`)])
    await refreshed('corpus-runs')
    assert.deepEqual((await countsOf('2')).find(row => row.text === '上候'), { text: '上候', n: 1, vertical: true }, 'a corpus run is counted with the crops\'')
    const glyphRun = await runOf('申上候')
    assert.deepEqual([glyphRun.total, glyphRun.items.map(o => o.crops.map(c => c.id))], [1, [['hl:run:0', 'hl:run:1', 'hl:run:2']]], 'a corpus run is listed')
    assert.equal(glyphRun.items[0].crops[1].source.title, 'A corpus book', 'its glyphs carry their published records')
    assert.equal(glyphRun.items[0].honkoku_url, null, 'a book that is no みんなで翻刻 entry has no page there')
    // An occurrence from a みんなで翻刻 entry links to its page: the first glyph to name one, here by its
    // 0-based page id, as honkoku-lines numbers them.
    {
      const entry = '0123456789abcdef0123456789abcdef'
      let entryPack = ''
      for (const [id, character, y, page] of [['hl:hk:0', '申', 0, null], ['hl:hk:1', '上', 12, `hl:${entry}:4`]]) {
        const raw = JSON.stringify({ id, origin: 'corpus', label: character, written_character: character, proxyable: true, state: 'pending', revision: 0,
          image: `/atlas/media/${id}.webp`, crop_box: { x: 10, y, w: 10, h: 10 }, source: { corpus: 'honkoku-lines', title: 'An entry', ...(page ? { page_id: page } : {}) } })
        await db.prepare(`INSERT INTO corpus_units(${CORPUS_COLUMNS},document) VALUES(?,?,?,?,?,?,?,?,?,?,?)`).bind(id, character, null, null, 5, 'pack-entry',
          new TextEncoder().encode(entryPack).length, new TextEncoder().encode(raw).length, 'unknown', 0, `hl:${entry}`).run()
        entryPack += raw
      }
      await bucket.put('pack-entry', entryPack)
      await db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document) VALUES('hl:hk:0',2,'hl:hk:1',NULL,'申上','hl:${entry}')`).run()
      await refreshed('entry-runs')
      const entryRun = await runOf('申上', `&document=hl%3A${entry}`)
      assert.deepEqual([entryRun.total, entryRun.items[0].honkoku_url], [1, `https://app.honkoku.org/transcription/${entry}/5`], 'an occurrence links to its みんなで翻刻 page')
      await db.batch([db.prepare("DELETE FROM unit_ngrams WHERE first='hl:hk:0'"), db.prepare("DELETE FROM corpus_units WHERE id LIKE 'hl:hk:%'")])
    }
    assert.equal(glyphRun.document, null)
    assert.equal((await runOf('申上', '&document=hl%3Abook')).total, 1, 'a corpus book holds its runs')
    // A corpus run takes its first glyph's group and shuffle, and follows its published row's production and style.
    const placed = async () => (await db.prepare("SELECT hand_order AS h,shuffle FROM unit_ngrams WHERE first='hl:run:0' AND size=2").first())
    assert.deepEqual(await placed(), { h: 1, shuffle: 5 }, 'unclassified, at its glyph\'s shuffle')
    await db.prepare("UPDATE corpus_units SET style='cursive' WHERE id='hl:run:0'").run()
    assert.deepEqual(await placed(), { h: 0, shuffle: 5 })
    await db.prepare("UPDATE corpus_units SET production='printed/type' WHERE id='hl:run:0'").run()
    assert.deepEqual(await placed(), { h: 2, shuffle: 5 }, 'type comes last whatever the style')
    await db.prepare("UPDATE corpus_units SET production='printed' WHERE id='hl:run:0'").run()
    await refreshed('corpus-runs-styled')
    const handRun = await runOf('申上', '&hand=hand')
    assert.deepEqual([handRun.total, handRun.hands.hand, handRun.works], [1, 1, [{ id: 'hl:book', title: 'A corpus book', count: 1 }]],
      'a corpus run is narrowed by group, and its book named from its glyph\'s record')
    // An occurrence whose record cannot be read is left off its page, which still pages on past it.
    await db.batch([
      db.prepare(`INSERT INTO corpus_units(${CORPUS_COLUMNS}) VALUES('hl:gone:0','申',NULL,NULL,5,'no-such-pack',0,10,'unknown',0),('hl:gone:1','上',NULL,NULL,5,'no-such-pack',10,10,'unknown',0)`),
      db.prepare("INSERT INTO unit_ngrams(first,size,second,third,text,document) VALUES('hl:gone:0',2,'hl:gone:1',NULL,'申上',NULL)"),
      db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at','corpus-runs-gone')")])
    const unread = await runOf('申上')
    assert.deepEqual([unread.total, unread.items.map(o => o.crops[0].id), unread.next_offset], [2, ['hl:run:0'], 2])
    await db.prepare("DELETE FROM corpus_units WHERE id LIKE 'hl:gone:%'").run()
    assert.deepEqual(await countsOf('3?document=hl%3Abook'), [{ text: '申上候', n: 1, vertical: true }], 'and counts them')
    // A publication files a glyph under another book: its runs and their counts follow.
    await db.prepare("UPDATE corpus_units SET document='hl:other' WHERE id='hl:run:0'").run()
    assert.deepEqual(await db.prepare("SELECT scope,n FROM ngram_counts WHERE size=3 AND text='申上候' ORDER BY scope").all().then(r => r.results),
      [{ scope: '', n: 1 }, { scope: 'hl:other', n: 1 }])
    await db.prepare("UPDATE corpus_units SET document='hl:book' WHERE id='hl:run:0'").run()
    // A decision moves an unnamed glyph's character.
    await db.prepare("UPDATE corpus_units SET character='下' WHERE id='hl:run:1'").run()
    assert.deepEqual([await textOf('hl:run:0'), await textOf('hl:run:1'), await textOf('hl:run:0', 3)], ['申下', '下候', '申下候'])
    assert.deepEqual([await countOf('申上'), await countOf('申下'), await countOf('申下候', 3)], [0, 1, 1], 'the counts follow the text')
    // A round names the glyph: its row's character stands for it, and the glyph reads its published one again once the row goes.
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('hl:run:1', 'corpus', '工', null, null, 'unknown', 'han',
      'checked', 1, 0, 1, 5, JSON.stringify({ id: 'hl:run:1', origin: 'corpus', label: '工', written_character: '工', state: 'checked', revision: 1 }), '{}', '{}', '{}', null).run()
    assert.equal(await textOf('hl:run:0'), '申工')
    await refreshed('corpus-runs-named')
    assert.deepEqual((await runOf('申工')).items.map(o => o.crops.map(c => [c.id, c.label])), [[['hl:run:0', '申'], ['hl:run:1', '工']]], 'a named glyph shows its row')
    await db.prepare("UPDATE units SET character='エ' WHERE id='hl:run:1'").run()
    assert.deepEqual([await textOf('hl:run:0'), (await db.prepare("SELECT document FROM unit_ngrams WHERE first='hl:run:1' AND size=2").first()).document], ['申エ', 'hl:book'],
      'a named glyph keeps its run\'s book')
    await db.prepare("DELETE FROM units WHERE id='hl:run:1'").run()
    assert.equal(await textOf('hl:run:0'), '申下')
    // A glyph that leaves the site takes its runs along.
    await db.prepare("DELETE FROM corpus_units WHERE id='hl:run:2'").run()
    assert.deepEqual([await textOf('hl:run:0'), await textOf('hl:run:1'), await textOf('hl:run:0', 3), await countOf('下候')], ['申下', null, null, 0])
    await db.prepare("DELETE FROM corpus_units WHERE id IN ('hl:run:0','hl:run:1')").run()
  }
  // A run is folded to its members' graphemes (0071) and found by what a reader types: ん followed by し
  // written in a hentaigana form (𛁅) is found as んし, and as ん𛁅, whose query folds the same way.
  await db.batch([['U+3057', 'し'], ['U+1B045', '𛁅']].map(([code, char]) => db.prepare('INSERT INTO characters(code_point,character,name,data,detail) VALUES(?,?,?,?,?)')
    .bind(code, char, '', JSON.stringify({ char, grapheme: { code_point: 'U+3057' } }), '{}')))
  // A query is folded through the characters' own index.
  const foldPlan = await plan({ sql: worker.runGraphemesQuery(), values: [] }, [JSON.stringify(['ん', '𛁅'])])
  assert.ok(foldPlan.some(d => /SEARCH c USING (COVERING )?INDEX character_text/.test(d)) && !foldPlan.some(d => /^SCAN (c|h)\b/.test(d)), foldPlan.join('; '))
  for (const [id, label] of [['fold-1', 'ん'], ['fold-2', '𛁅'], ['fold-3', 'て'], ['fold-4', '𛁅']]) {
    const d = { id, label, state: 'pending', revision: 0, image_sha256: hash, production: 'unknown', repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', label, null, null, 'unknown', 'kana', 'pending', 0, 1, 1, 3, JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  await db.prepare(`INSERT INTO unit_ngrams(first,size,second,third,text,document) VALUES('fold-1',2,'fold-2',NULL,'ん𛁅',NULL),
    ('fold-2',2,'fold-3',NULL,'𛁅て',NULL),('fold-3',2,'fold-4',NULL,'て𛁅',NULL),('fold-1',3,'fold-2','fold-3','ん𛁅て',NULL),
    ('fold-2',3,'fold-3','fold-4','𛁅て𛁅',NULL)`).run()
  assert.equal((await db.prepare("SELECT graphemes FROM unit_ngrams WHERE first='fold-1'").first()).graphemes, 'んし', 'a run is folded as it is written')
  await db.prepare("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at','fold')").run()
  const folded = await runOf('んし'), written = await runOf('ん𛁅')
  assert.deepEqual([folded.total, folded.items.map(o => o.crops.map(c => c.label))], [1, [['ん', '𛁅']]], 'a run is found by its graphemes and shown as written')
  assert.equal(written.total, 1, 'a written form folds to the same graphemes')
  assert.deepEqual((await runOf('んして')).items.map(o => o.crops.map(c => c.id)), [['fold-1', 'fold-2', 'fold-3']], 'a trigram is folded too')
  assert.deepEqual((await runOf('んしてし')).items.map(o => o.crops.map(c => c.id)), [['fold-1', 'fold-2', 'fold-3', 'fold-4']],
    'a longer run chains folded pairs')
  // A review that relabels a member folds the run again.
  await db.prepare("UPDATE units SET character='か' WHERE id='fold-2'").run()
  assert.equal((await db.prepare("SELECT graphemes FROM unit_ngrams WHERE first='fold-1'").first()).graphemes, 'んか')
  await db.batch([db.prepare("DELETE FROM unit_ngrams WHERE first LIKE 'fold-%'"), db.prepare("DELETE FROM units WHERE id LIKE 'fold-%'"),
    db.prepare("DELETE FROM characters WHERE code_point IN ('U+3057','U+1B045')")])
  // A run filed under an empty book is counted once, on the whole site.
  await db.prepare("INSERT INTO unit_ngrams(first,size,second,text,document) VALUES('blank1',2,'blank2','空白','')").run()
  assert.deepEqual((await db.prepare("SELECT scope,n FROM ngram_counts WHERE text='空白'").all()).results, [{ scope: '', n: 1 }])
  await db.prepare('DELETE FROM unit_ngrams').run()
  assert.equal((await db.prepare('SELECT count(*) AS n FROM ngram_counts').first()).n, 0, 'every count goes with its runs')
  for (const [index, create] of Object.entries(keys)) {
    await db.prepare(`DROP INDEX ${index}`).run()
    for (const [shape, bound] of shapes.filter(s => s[2] === index))
      await assert.rejects(async () => served(await plan(shape, bound), index), `the check on ${index} fails without it`)
    await db.prepare(create).run()
  }
  // A row published as `other` before Hangul had a category reads `hangul` once 0008 has run, and
  // the listing filters it by that group.
  const jamo = { id: 'hangul', label: 'ㅿ', state: 'pending', revision: 0, image_sha256: hash,
    production: 'printed/woodblock', repair: { quiz: true } }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    jamo.id, 'local', 'ㅿ', 'U+317F', null, 'printed/woodblock', 'other', 'pending', 0, 1, 1, 1,
    JSON.stringify(jamo), JSON.stringify({ character: jamo }), '{}', '{}', null).run()
  await apply('0008_hangul_category.sql')
  assert.deepEqual((await call('/atlas?group=hangul')).items.map(i => i.id), ['hangul'], 'a Hangul label is in the hangul group')
  assert.ok(!(await call('/atlas?group=kana')).items.some(i => i.id === 'hangul'), 'and in no other')
  // A row published as `other` before gugyeol had a category reads `gugyeol` once 0012 has run, and
  // the listing filters it by that group.
  const gugyeol = { id: 'gugyeol', label: '', state: 'pending', revision: 0, image_sha256: hash,
    production: 'printed/woodblock', repair: { quiz: true } }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    gugyeol.id, 'local', '', 'U+F67F', null, 'printed/woodblock', 'other', 'pending', 0, 1, 1, 1,
    JSON.stringify(gugyeol), JSON.stringify({ character: gugyeol }), '{}', '{}', null).run()
  await apply('0012_gugyeol_category.sql')
  assert.deepEqual((await call('/atlas?group=gugyeol')).items.map(i => i.id), ['gugyeol'], 'a gugyeol label is in the gugyeol group')
  assert.ok(!(await call('/atlas?group=kana')).items.some(i => i.id === 'gugyeol'), 'and in no other')
  // Explore narrows to one book: the listing counts crops per book, and `document` lists one book's.
  // Crops written straight to D1 and stamped, as a refresh writes them, show in a reviewer's counts.
  for (const [id, book, title] of [['book-a1', 'hl:A', '甲'], ['book-a2', 'hl:A', '甲'], ['book-b1', 'hl:B', '乙']]) {
    const d = { id, label: 'ヌ', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten',
      source: title, page_id: `${book}:1`, repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', 'ヌ', 'U+30CC', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
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
    const d = { id, label, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten', repair: { quiz: true } }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      id, 'local', label, family, null, 'handwritten', 'kanji', 'pending', 0, 1, 1, 1,
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
  const framed = { id: 'framed', label: 'カ', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten',
    repair: { quiz: true }, context: true, context_image: '/atlas/media/narrow.webp', context_box: { x: 5, y: 5, w: 40, h: 60 } }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    framed.id, 'local', 'カ', 'U+30AB', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
    JSON.stringify(framed), JSON.stringify({ character: framed }), '{}', '{}', null).run()
  const wide = { x: 0, y: 0, w: 90, h: 120 }
  const framedRound = { id: crypto.randomUUID(), grapheme: 'U+30AB',
    answers: [{ id: 'framed', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'blank' }] }
  await call('/atlas/rounds', framedRound)
  await db.prepare(`UPDATE units SET data=json_set(data,'$.context_image',json('"/atlas/media/wide.webp"'),'$.context_box',json(?)) WHERE id='framed' AND revision=1`)
    .bind(JSON.stringify(wide)).run()
  await call(`/atlas/rounds/${framedRound.id}/undo`, {})
  const unframed = await call('/atlas/characters/framed')
  assert.deepEqual([unframed.state, unframed.context_image, unframed.context_box], ['pending', '/atlas/media/wide.webp', wide],
    'undo restores the review state and keeps the wider context')
  // GET /atlas/history: every review and undo, and each standing round with the crops it passed, newest
  // first, filterable by user or by label; another kind of event never appears though it sits in the same table.
  const get = async path => { const response = await mf.dispatchFetch(base + path); assert.equal(response.status, 200); return response.json() }
  const all = await get('/atlas/history?limit=100')
  assert.ok(all.items.every(i => ['review', 'undo', 'passed'].includes(i.kind)), 'only reviews, undos and passed rounds are listed')
  assert.ok(all.items.some(i => i.kind === 'passed') && all.items.filter(i => i.kind === 'passed').every(i => i.passed > 0 && i.target === null),
    'a passed round names how many crops it passed')
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
  const vetted = { id: 'vetted', label: 'キ', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten',
    repair: { status: 'joined', quiz: true } }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    vetted.id, 'local', 'キ', 'U+30AD', null, 'handwritten', 'kana', 'pending', 0, 1, 1, 1,
    JSON.stringify(vetted), JSON.stringify({ character: vetted }), '{}', '{}', null).run()
  const vettedRound = { id: crypto.randomUUID(), grapheme: 'U+30AD',
    answers: [{ id: 'vetted', revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'blank' }] }
  await call('/atlas/rounds', vettedRound)
  await db.prepare(`UPDATE units SET data=json_set(data,'$.repair',json('{"status":"uncertain","quiz":false}')), quiz=0 WHERE id='vetted' AND revision=1`).run()
  await call(`/atlas/rounds/${vettedRound.id}/undo`, {})
  const vettedRow = await db.prepare("SELECT quiz, json_extract(data,'$.repair.quiz') AS dealt, json_extract(data,'$.state') AS state FROM units WHERE id='vetted'").first()
  assert.deepEqual(vettedRow, { quiz: 0, dealt: 0, state: 'pending' }, 'undo restores the review state and keeps the newer repair verdict out of the quiz')
  // A retired crop names the crop that replaced it: deleted, kept for its history, or through a chain.
  const retiredCrop = { id: 'retired-kept', label: 'ア', state: 'flagged', revision: 1, image_sha256: hash, production: 'handwritten' }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
    retiredCrop.id, 'retired', 'ア', 'U+30A2', null, 'handwritten', 'kana', 'flagged', 1, 0, 1, 1,
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
    const d = { id, label: character, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(id, 'local', character, family, null,
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
    const d = { id, label: '仮', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS},style) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(id, 'local', '仮', 'U+4EEE', null,
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
    db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES('sty-corpus','corpus','仮','U+4EEE',NULL,'unknown','kanji','checked',1,0,1,9,'{}','{}','{}','{}',NULL)`)])
  await db.prepare("UPDATE corpus_units SET style='running' WHERE id='sty-corpus'").run()
  assert.equal((await db.prepare("SELECT style FROM units WHERE id='sty-corpus'").first()).style, 'running')
  await db.batch([db.prepare("DELETE FROM units WHERE id='sty-corpus'"), db.prepare("DELETE FROM corpus_units WHERE id='sty-corpus'")])
  await db.prepare("DELETE FROM units WHERE id LIKE 'sty-%'").run()
  // Search finds a crop by its written character, each once.
  for (const [id, character] of [['find-kana', 'と'], ['find-ligature', '𪜈'], ['find-other', 'も']]) {
    const d = { id, label: character, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
    await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(id, 'local', character, null, null,
      'handwritten', 'kana', 'pending', 0, 0, 1, 1, JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
  }
  const searched = async term => { const found = await call(`/atlas?q=${encodeURIComponent(term)}&limit=96`); return [found.total, found.items.map(i => i.id).filter(id => id.startsWith('find-')).sort()] }
  const matching = async term => (await db.prepare("SELECT count(*) AS n FROM units WHERE origin='local' AND character=?").bind(term).first()).n
  assert.deepEqual(await searched('𪜈'), [await matching('𪜈'), ['find-ligature']], 'a search matches the written character')
  assert.deepEqual(await searched('とも'), [0, []], 'typed kana reach a character through the alias index, not the crop search')
  // A picked character within a script: the script still filters.
  const pickedIn = async group => (await call(`/atlas?character=${encodeURIComponent('仮')}&group=${group}&limit=96`)).items.map(i => i.id).filter(id => id.startsWith('fam-')).sort()
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
  for (const [query, where] of [['', '1=1'], ['character=仮', "character='仮'"], ['document=hk:tally', "document='hk:tally'"],
    ['state=checked', "state='checked'"], ['character=仮&document=hk:tally&state=pending', "character='仮' AND document='hk:tally' AND state='pending'"]])
    assert.equal((await call(`/atlas?${encodeURI(query)}&limit=1`)).total, await localCount(where), `browse ${query || 'everything'} totals what it lists`)
  // A crop written without a stamp is not in those counts yet: the listing did not count the table.
  const listedBefore = (await call('/atlas?limit=1')).total, pickedBefore = (await call(`/atlas?${encodeURI('character=仮')}&limit=1`)).total
  const unstamped = { id: 'unstamped', label: '仮', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten' }
  await db.prepare(`INSERT INTO units(${CROP_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind('unstamped', 'local', '仮', null, null,
    'handwritten', 'kanji', 'pending', 0, 0, 1, 1, JSON.stringify(unstamped), JSON.stringify({ character: unstamped }), '{}', '{}', null).run()
  assert.equal((await call('/atlas?limit=1')).total, listedBefore, 'an unfiltered browse page does not count the table')
  assert.equal((await call(`/atlas?${encodeURI('character=仮')}&limit=1`)).total, pickedBefore, 'nor does one filtered by character')
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
        const { where, values } = worker.listingFilter(review, review ? 'not:printed/type' : 'all', character && [character])
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
    const d = { id, label, state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten', repair: { quiz: true }, ...extra }
    await db.prepare(`INSERT INTO units(id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document)
      VALUES(?,'local',?,NULL,NULL,'handwritten','kana',?,0,1,1,1,?,?,'{}','{}',NULL)`).bind(id, label, d.state, JSON.stringify(d), JSON.stringify({ character: d })).run()
  }
  await addLocal('batch-a', 'ア'); await addLocal('batch-b', 'ウ')
  await addLocal('batch-crop', 'エ', { state: 'flagged', issue: 'crop' })
  await addLocal('batch-same', 'タ'); await addLocal('batch-done', 'タ', { state: 'checked', written_character: 'タ' })
  // Two corpus glyphs: one that can be shown, one whose image the site may not show.
  for (const [id, proxyable, shuffle] of [['codh:batch', true, 3], ['codh:hidden', false, 4]]) {
    const record = JSON.stringify({ id, origin: 'corpus', label: 'ウ', source_label: 'ウ', written_character: 'ウ', identity_status: 'assigned',
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
  const fullRound = { id: crypto.randomUUID(), grapheme: 'U+30BD', seen: [], skipped: [],
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
  // A crop's form: the picker's value names a representation and its form, and the crop gets a
  // form claim; its character, state and revision stay as they are, and every listing shows the form.
  await addLocal('form-local', '還', { box: { x: 1, y: 2, w: 3, h: 4 } })
  const formPath = '/atlas/characters/form-local/form'
  const formVersion = (await call('/atlas/characters/form-local')).crop_version
  assert.equal((await call('/atlas/characters/form-local')).form, null, 'a crop nobody has looked at is unsorted')
  const formSave = { id: crypto.randomUUID(), crop_version: formVersion, form: '⿺辶𦊷' }
  const formed = await call(formPath, formSave)
  assert.deepEqual([formed.form.status, formed.form.values.map(v => [v.scheme, v.text]), formed.form.by, formed.replaced],
    ['asserted', [['ids', '⿺辶𦊷']], [(await user('integration')).id], null], 'the form names who holds it, and a first save replaces nothing')
  const inspectedForm = await call('/atlas/characters/form-local')
  assert.deepEqual([inspectedForm.form.values[0].text, inspectedForm.label, inspectedForm.state, inspectedForm.revision], ['⿺辶𦊷', '還', 'pending', 0], 'the inspector reads it')
  assert.equal((await call('/atlas?character=' + encodeURIComponent('還'))).items.find(item => item.id === 'form-local').form.values[0].text, '⿺辶𦊷', 'a listing reads it')
  assert.deepEqual(await call(formPath, formSave), formed, 'a retry answers with the crop\'s form')
  await call(formPath, { ...formSave, form: '𮟃' }, 409)
  for (const form of ['⿺辶', '⿰木木木', '還還', 'a⿰', '⿰木a', ' '.repeat(3) + '⿰', '\uE000'])
    await call(formPath, { ...formSave, id: crypto.randomUUID(), form }, 422)
  await call(formPath, { ...formSave, id: crypto.randomUUID(), review: 'not-a-review' }, 422)
  await call(formPath, { ...formSave, id: crypto.randomUUID(), crop_version: 'form-local@' + 'c'.repeat(64) + '@1,2,3,4' }, 409)
  const variant = await call(formPath, { ...formSave, id: crypto.randomUUID(), form: 'U+2E7C3' })
  assert.deepEqual([variant.form.values.map(v => [v.scheme, v.text]), variant.replaced], [[['unicode', '𮟃']], '⿺辶𦊷'],
    'a code point is read as its character, and replaces the reviewer\'s own, which the answer names')
  const [formRow] = (await db.prepare("SELECT id,anchor FROM forms WHERE id=?").bind(variant.form.values[0].form).all()).results
  assert.equal((await db.prepare('SELECT value FROM representations WHERE id=?').bind(formRow.anchor).first()).value, '𮟃')
  // Choosing the crop's own character confirms it: that is a form claim too, never a clear.
  const confirmed = await call(formPath, { ...formSave, id: crypto.randomUUID(), form: '還' }, 200, 'inspector')
  assert.deepEqual([confirmed.form.status, confirmed.form.values.map(v => v.text)], ['disputed', ['𮟃', '還']], 'two reviewers who see different forms both stand')
  // A review saved against the revision the crop was opened at still stands, and keeps the form.
  await call('/atlas/characters/form-local', { id: crypto.randomUUID(), revision: 0, image_sha256: hash, verdict: 'match' })
  const reviewedForm = await call('/atlas/characters/form-local')
  assert.deepEqual([reviewedForm.state, reviewedForm.revision, reviewedForm.form.status], ['checked', 1, 'disputed'])
  const cleared = await call(formPath, { id: crypto.randomUUID(), crop_version: formVersion, form: null })
  assert.deepEqual([cleared.form.status, cleared.form.values.map(v => v.text), cleared.replaced], ['asserted', ['還'], '𮟃'], 'clearing retracts the reviewer\'s own form')
  await call(formPath, { id: crypto.randomUUID(), crop_version: formVersion, form: null }, 409)
  // The same value chosen again names the same form; the form was named once, by the reviewer who chose it first.
  assert.deepEqual((await db.prepare("SELECT tier FROM assertions WHERE predicate='represented_by' AND subject=?").bind(formRow.id).all()).results, [{ tier: 'observed' }])
  // A refused save writes nothing, not even a corpus glyph's row.
  const unnamed = (await call('/atlas/corpus/character?id=nu-private')).crop_version
  await call('/atlas/characters/nu-private/form', { id: crypto.randomUUID(), crop_version: unnamed, form: '⿰木' }, 422)
  await call('/atlas/characters/nu-private/form', { id: crypto.randomUUID(), crop_version: unnamed, form: null }, 409)
  assert.equal(await db.prepare("SELECT 1 FROM units WHERE id='nu-private'").first(), null)
  // A corpus glyph nothing has named gets its `units` row, and stays unflagged and unreviewed.
  const glyphVersion = (await call('/atlas/corpus/character?id=na-5')).crop_version
  const glyphForm = await call('/atlas/characters/na-5/form', { id: crypto.randomUUID(), crop_version: glyphVersion, form: '⿱十乚' })
  assert.equal(glyphForm.form.values[0].text, '⿱十乚')
  assert.deepEqual(await db.prepare("SELECT origin,state FROM units WHERE id='na-5'").first(), { origin: 'corpus', state: 'pending' })
  assert.equal((await call('/atlas/corpus/character?id=na-5')).form.values[0].text, '⿱十乚')
  const naRow = { char: 'ナ', code_point: 'U+30CA', candidates: {} }
  await db.prepare('INSERT OR IGNORE INTO characters VALUES(?,?,?,?,?)').bind('U+30CA', 'ナ', '', JSON.stringify(naRow), JSON.stringify(naRow)).run()
  assert.equal((await call('/layers/candidates?code_point=U%2B30CA&limit=200')).glyph_items.find(item => item.id === 'na-5')?.form?.values[0].text, '⿱十乚',
    'a corpus gallery reads it')
  assert.equal((await db.prepare("SELECT count(*) AS n FROM events WHERE target IN ('na-5','form-local') AND json_extract(json_extract(event,'$.evidence'),'$.kind')!='character-review'").first()).n, 0,
    'a form writes no review event')
  assert.equal(await db.prepare('SELECT count(*) AS n FROM written_forms').first('n'), 0, 'nothing writes the old journal')
  for (const [sql, bound] of [[worker.cropFormsQuery(), [JSON.stringify(['form-local', 'na-5'])]], [worker.formNamesQuery(), [JSON.stringify([formRow.id])]]]) {
    const plan = (await db.prepare('EXPLAIN QUERY PLAN ' + sql).bind(...bound).all()).results.map(row => row.detail)
    assert.ok(!plan.some(d => /^SCAN (c|u|f|r|current_claims|units|forms|representations)\b/.test(d) && !/USING (COVERING )?INDEX|USING INTEGER PRIMARY KEY/.test(d)), plan.join('; '))
  }
  // A reviewer redraws a local crop's box: inside the page view, on the pixels it names, as the fix.
  {
    const d = { id: 'recrop', label: 'ア', reading: 'ア', state: 'flagged', issue: 'crop', revision: 0, image_sha256: hash,
      production: 'handwritten', repair: { quiz: false }, box: { x: 100, y: 200, w: 40, h: 50 }, context: true,
      context_box: { x: 80, y: 360, w: 120, h: 240 }, crop_box: { x: 100, y: 400, w: 40, h: 100 }, source_scale: [1, 2] }
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      // Not dealt to rounds (`quiz` 0), so the listings checked further on keep their crops.
      'recrop', 'local', 'ア', 'ア', 'U+3042', null, 'handwritten', 'kana', 'flagged', 0, 0, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
    assert.equal((await call('/atlas/characters/recrop')).crop_editable, true, 'a local crop with a page view can be redrawn')
    const fix = box => ({ id: crypto.randomUUID(), revision: 0, image_sha256: hash, verdict: 'match', box })
    await call('/atlas/characters/recrop', fix({ x: 79, y: 190, w: 40, h: 50 }), 422)
    await call('/atlas/characters/recrop', fix({ x: 90, y: 190, w: 120, h: 50 }), 422)
    await call('/atlas/characters/recrop', fix({ x: 90, y: 190, w: 40.5, h: 50 }), 422)
    await call('/atlas/characters/recrop', { ...fix({ x: 90, y: 190, w: 40, h: 50 }), verdict: 'wrong', issue: 'crop' }, 422)
    await call('/atlas/characters/recrop', { ...fix({ x: 90, y: 190, w: 40, h: 50 }), image_sha256: 'c'.repeat(64) }, 409)
    const redraw = fix({ x: 90, y: 190, w: 44, h: 60 }), redrawId = redraw.id
    await call('/atlas/characters/recrop', redraw)
    const redrawn = await call('/atlas/characters/recrop')
    assert.deepEqual([redrawn.box, redrawn.box_pending, redrawn.state, redrawn.revision], [{ x: 90, y: 190, w: 44, h: 60 }, true, 'checked', 1],
      'the redrawn box is the crop\'s, awaiting the next cut, and the crop is fixed')
    const row = (await call('/atlas/reviews.json')).reviews.filter(r => r.event.target_id === 'recrop').pop()
    const evidence = JSON.parse(row.event.evidence)
    assert.deepEqual(evidence.recrop, { from: { x: 100, y: 200, w: 40, h: 50 }, to: { x: 90, y: 190, w: 44, h: 60 }, pixels: hash },
      'the review records the box claim and the evidence it was made on')
    assert.deepEqual(evidence.correction.box, { x: 90, y: 190, w: 44, h: 60 })
    await call('/atlas/corrections', { id: crypto.randomUUID(), character: 'ア',
      crops: [{ id: 'recrop', revision: 1, image_sha256: hash, box: { x: 90, y: 190, w: 40, h: 50 } }] }, 422)
    // Undoing the redraw puts the old box back, and nothing awaits a cut.
    await call(`/atlas/rounds/${redrawId}/undo`, {})
    const undone = await call('/atlas/characters/recrop')
    assert.deepEqual([undone.box, undone.box_pending ?? false], [{ x: 100, y: 200, w: 40, h: 50 }, false], 'undo restores the box')
    const recropVersions = await call('/atlas/characters/recrop/versions')
    assert.deepEqual([recropVersions.versions.map(v => v.box), recropVersions.current], [['100,200,40,50', '90,190,44,60'], `recrop@${hash}@100,200,40,50`],
      'a redrawn box is a version of its own, and undoing it returns to the first')
    assert.equal((await call('/atlas/corpus/character?id=' + encodeURIComponent(corpus.id))).crop_editable ?? false, false, 'a corpus glyph keeps its source box')
  }
  // Crop evidence versions: a crop reports the one it has, and a recrop keeps the one reviewed before.
  await addLocal('versioned', '仮', { box: { x: 10, y: 20, w: 30, h: 40 }, image: '/atlas/media/' + 'c'.repeat(64) + '.webp' })
  const firstVersion = `versioned@${hash}@10,20,30,40`
  assert.equal((await call('/atlas/characters/versioned')).crop_version, firstVersion, 'the inspector names the version')
  await call('/atlas/characters/versioned', { id: crypto.randomUUID(), revision: 0, image_sha256: hash, verdict: 'match' })
  assert.equal((await call('/atlas/characters/versioned/versions')).versions.length, 1, 'a review records no new version')
  const recropped = { ...JSON.parse((await db.prepare("SELECT data FROM units WHERE id='versioned'").first()).data), box: { x: 10, y: 20, w: 31, h: 40 } }
  await db.prepare("UPDATE units SET data=? WHERE id='versioned'").bind(JSON.stringify(recropped)).run()
  const versions = await call('/atlas/characters/versioned/versions')
  assert.equal(versions.current, `versioned@${hash}@10,20,31,40`)
  assert.deepEqual(versions.versions.map(v => [v.id, v.pixels, v.box]), [[firstVersion, hash, '10,20,30,40'], [versions.current, hash, '10,20,31,40']],
    'the version reviewed before the recrop is still there')
  const versionPlan = (await db.prepare('EXPLAIN QUERY PLAN ' + worker.cropVersionsQuery()).bind('versioned').all()).results.map(row => row.detail).join(' | ')
  assert.ok(/crop_version_unit/.test(versionPlan) && !/TEMP B-TREE/.test(versionPlan), 'versions are read in index order: ' + versionPlan)
  await assert.rejects(db.prepare("UPDATE crop_versions SET image=NULL WHERE unit='versioned'").run(), /crop_version_immutable/)
  assert.equal(await db.prepare("SELECT 1 FROM units WHERE id='na-unassigned'").first(), null)
  assert.equal((await call('/atlas/corpus/character?id=na-unassigned')).crop_version,
    `na-unassigned@${createHash('sha256').update('na-unassigned').digest('hex')}@1,2,3,4`, 'a corpus glyph with no row yet names the version its row would have')
  // The assertion ledger: claims, actions, their resolution, a recrop, the export and its query plans.
  // Saves are made as signed-in reviewers (`call`'s fourth argument), and `judge` is an admin.
  await addLocal('claimed', '仮', { box: { x: 1, y: 2, w: 3, h: 4 } })
  const claimedVersion = (await call('/atlas/characters/claimed')).crop_version
  const claimOn = (fields = {}) => ({ id: crypto.randomUUID(), subject: 'claimed', predicate: 'has_form', crop_version: claimedVersion, claims: [{ value: 'unresolved' }], ...fields })
  const firstClaim = claimOn()
  const made = await call('/atlas/claims', firstClaim)
  assert.deepEqual([made.current[0].status, made.current[0].value, made.current[0].supporting], ['asserted', 'unresolved', made.assertions])
  assert.deepEqual(await call('/atlas/claims', firstClaim), made, 'a retried claim answers with the first')
  await call('/atlas/claims', { ...firstClaim, claims: [{ value: 'unreadable' }] }, 409)
  await call('/atlas/claims', claimOn({ crop_version: 'claimed@x@1,2,3,4' }), 409)
  for (const bad of [{ claims: [{ value: 'legible' }] }, { predicate: 'reads_as' }, { claims: [] }, { claims: [{ value: 'unresolved', confidence: 0.4 }] }])
    await call('/atlas/claims', claimOn(bad), 422)
  const rival = await call('/atlas/claims', claimOn({ claims: [{ value: 'unreadable' }] }), 200, 'inspector')
  assert.deepEqual([rival.current[0].status, rival.current[0].value, rival.current[0].claims.length], ['disputed', null, 2], 'two people disagree')
  await call(`/atlas/claims/${made.assertions[0]}/actions`, { id: crypto.randomUUID(), action: 'retract' }, 403, 'inspector')
  await call(`/atlas/claims/${made.assertions[0]}/actions`, { id: crypto.randomUUID(), action: 'accept' }, 422)
  await call(`/atlas/claims/${made.assertions[0]}/actions`, { id: crypto.randomUUID(), action: 'adjudicate' }, 403, 'bob')
  await db.prepare(`UPDATE "user" SET role='admin' WHERE id=?`).bind((await user('judge')).id).run()
  const decided = await call(`/atlas/claims/${rival.assertions[0]}/actions`, { id: crypto.randomUUID(), action: 'adjudicate', reason: 'the ink is lost' }, 200, 'judge')
  assert.deepEqual([decided.current[0].status, decided.current[0].value, decided.current[0].supporting], ['adjudicated', 'unreadable', rival.assertions])
  // A reviewer's new claim retracts their own earlier one; an alternative set is one claim.
  const changed = await call('/atlas/claims', claimOn({ claims: [{ value: 'unresolved', confidence: 0.6, confidence_scheme: 'reviewer-weight' },
    { value: 'unreadable', confidence: 0.4, confidence_scheme: 'reviewer-weight' }] }))
  assert.deepEqual(changed.retracted, made.assertions)
  await call(`/atlas/claims/${rival.assertions[0]}/actions`, { id: crypto.randomUUID(), action: 'retract' }, 200, 'inspector')
  const [set] = (await call('/atlas/claims?subject=claimed')).current
  assert.deepEqual([set.status, set.object, set.members.map(m => [m.value, m.confidence])], ['asserted', null, [['unreadable', 0.4], ['unresolved', 0.6]]])
  await call(`/atlas/claims/${changed.assertions[1]}/actions`, { id: crypto.randomUUID(), action: 'accept' }, 200, 'bob')
  assert.equal((await call('/atlas/claims?subject=claimed')).current[0].status, 'accepted', 'accepting one alternative accepts the set')
  // A recrop leaves no claim standing; the claims stay to be read.
  await db.prepare("UPDATE units SET data=json_set(data,'$.box.w',5) WHERE id='claimed'").run()
  const recutClaims = await call("/atlas/claims?subject=claimed")
  assert.deepEqual([recutClaims.current, recutClaims.history.length], [[], 4], 'the recut crop is unsorted and its claims stay')
  assert.deepEqual(recutClaims.history[0].evidence, [{ kind: 'crop', ref: claimedVersion, locator: null }])
  assert.equal(await db.prepare("SELECT count(*) AS n FROM current_claims WHERE subject='claimed'").first('n'), 0, 'the resolved row went with the old cut')
  // A corpus glyph nothing has named gets its row with its first claim.
  const glyphClaim = await call('/atlas/claims', { id: crypto.randomUUID(), subject: 'nu-shown', predicate: 'has_form',
    crop_version: (await call('/atlas/corpus/character?id=nu-shown')).crop_version, claims: [{ value: 'unreadable' }] })
  assert.equal(glyphClaim.current[0].value, 'unreadable')
  assert.equal((await db.prepare("SELECT origin,state FROM units WHERE id='nu-shown'").first()).state, 'pending', 'the glyph stays unreviewed')
  // The export, a page at a time, and every ledger read by its index.
  const firstPage = await call('/atlas/ledger?limit=3')
  assert.deepEqual([firstPage.kind, firstPage.assertions.length, firstPage.done], ['atlas-ledger', 3, false])
  const laterPage = await call(`/atlas/ledger?after=${firstPage.next.after}&actions_after=${firstPage.next.actions_after}`)
  assert.equal(firstPage.assertions.length + laterPage.assertions.length, await db.prepare('SELECT count(*) AS n FROM assertions').first('n'))
  assert.ok(laterPage.done && laterPage.actions.length + firstPage.actions.length === await db.prepare('SELECT count(*) AS n FROM assertion_actions').first('n'),
    'every claim and action, a page at a time')
  for (const [sql, bound] of [[worker.ledgerClaimsQuery(), [0, 3]], [worker.ledgerActionsQuery(), [0, 3]], [worker.claimHistoryQuery(), ['claimed']],
    [worker.currentClaimsQuery(), ['claimed']], [worker.resolveWriteQuery(), [JSON.stringify([['claimed', 'has_form', '', '']])]], [worker.resolveClearQuery(), [JSON.stringify([['claimed', 'has_form', '', '']])]]]) {
    // No ledger table, nor `units`, is read whole or through an index built for the query; the
    // resolver's own intermediate results may be.
    const plan = (await db.prepare('EXPLAIN QUERY PLAN ' + sql).bind(...bound).all()).results.map(row => row.detail)
    const table = /^(SCAN|SEARCH) (a|x|e|p|u|assertions|assertion_actions|assertion_evidence|assertion_premises|current_claims|units)\b/
    assert.ok(!plan.some(d => table.test(d) && (/AUTOMATIC/.test(d) || (d.startsWith('SCAN') && !/USING (COVERING )?INDEX|USING INTEGER PRIMARY KEY/.test(d)))), plan.join('; '))
  }
  // ば's grapheme holds ば, バ and each hentaigana of は with U+3099. A round lists them, a sequence
  // member is refused as a round of its own, and a crop marked as 𛂞 + U+3099 stays filed under ば.
  {
    const ha = '\u{1B09E}\u3099', key = point => [...point].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase()).join(' ')
    const grapheme = { code_point: 'U+3070', char: 'ば', members: ['ば', 'バ', ha].map(char => ({ char, code_point: key(char) })) }
    for (const { char, code_point } of grapheme.members) {
      const data = { char, code_point, grapheme, candidates: {} }
      await db.prepare('INSERT INTO characters VALUES(?,?,?,?,?)').bind(code_point, char, '', JSON.stringify(data), JSON.stringify(data)).run()
    }
    const d = { id: 'ba-1', label: 'ば', reading: 'ば', state: 'pending', revision: 0, image_sha256: hash, production: 'handwritten', repair: { quiz: false } }
    await db.prepare(`INSERT INTO units(${UNIT_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).bind(
      'ba-1', 'local', 'ば', 'ば', 'U+3070', null, 'handwritten', 'kana', 'pending', 0, 0, 1, 1,
      JSON.stringify(d), JSON.stringify({ character: d }), '{}', '{}', null).run()
    assert.deepEqual((await call('/atlas?purpose=review&grapheme=U%2B3070')).grapheme.members, ['ば', 'バ', ha], 'a round lists the sequence member')
    await call('/atlas?purpose=review&grapheme=' + encodeURIComponent(key(ha)), undefined, 422)
    assert.deepEqual((await call('/atlas?purpose=review&grapheme=U%2B306F%20U%2B3099')).grapheme.members, ['ば'], 'a key with no row is its text, composed')
    const card = await call('/layers/characters/' + encodeURIComponent(key(ha)))
    assert.equal(card.grapheme.code_point, 'U+3070', 'a sequence member has a card of its own, under ば')
    await call('/layers/units/ba-1', { id: crypto.randomUUID(), revision: 0, image_sha256: hash, verdict: 'wrong', issue: 'character', character: ha })
    assert.deepEqual(await db.prepare("SELECT character,family FROM units WHERE id='ba-1'").first(), { character: ha, family: 'U+3070' },
      'a crop marked as a sequence member stays filed under its grapheme')
  }
  // One address is held to a number of claims a minute.
  let claimsLimited = false
  for (let i = 0; i < 120 && !claimsLimited; i++) {
    const response = await mf.dispatchFetch(base + formPath, { method: 'POST', headers: { 'content-type': 'application/json', origin: base, cookie: (await user('integration')).cookie }, body: '{}' })
    claimsLimited = response.status === 429
  }
  assert.ok(claimsLimited, 'claims are rate-limited per address')
  // One address gets 20 batches a minute.
  let rateLimited = false
  for (let i = 0; i < 25 && !rateLimited; i++) {
    const response = await mf.dispatchFetch(base + '/atlas/corrections', { method: 'POST', headers: { 'content-type': 'application/json', origin: base, cookie: users.integration.cookie }, body: '{}' })
    rateLimited = response.status === 429
  }
  assert.ok(rateLimited, 'batches are rate-rateLimited per address')
  console.log('Workerd integration passed: atomic rounds, issue-only saves, retries, undo, corpus identity, search, gallery, export, seen crops, flagged order, corpus rounds, edit history, hosted forms, batch corrections, redrawn boxes, crop versions, the assertion ledger, crop forms.')
} finally {
  await mf.dispose()
  await rm(bundleDir, { recursive: true, force: true })
}
