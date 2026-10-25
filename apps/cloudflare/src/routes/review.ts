// Review writes on the new storage layout (decision records 0005 and 0006): rounds, batch
// corrections, single reviews of a crop or a corpus glyph, and their undo. Each request is one batch
// in `ATLAS`: its submission, an event per crop it changes, the crops' rows, their counts in `tally`,
// the `reviews` overlay, seen marks and skips, and the reviewer's totals.
import { Problem } from '../busy';
import { captureReviews, priorBaselines, type ReviewedInput } from '../reviewCapture';
import { cropEntries, cropItems, publicId } from '../atlas3';
import { STALE_CODE, StaleCrop, cropParam, writtenCropField } from '../atlas3Crops';
import { categoryOf } from '../category';
import { formOfCrop } from './claims';
import { body, canonical, compose, graphemeKey, graphemeText, literal, text, writtenCharacter } from '../input';
import { readRecords, recordKey } from '../records';
import { addressKey } from '../reverse';
import { ROUND_MAX } from '../rounds';

type Json = Record<string, any>;

// Event kinds and states, as `codes` numbers them (0001, 0003, 0009).
export const KIND = { match: 0, wrong: 1, unsure: 2, seen: 3, hard: 4, undo: 5 } as const;
const VERDICTS = [KIND.match, KIND.wrong, KIND.unsure];
const STATE = { pending: 0, checked: 1, flagged: 2, seen: 3, hard: 4 } as const;
const ISSUES = ['character', 'merged', 'crop', 'blank', 'other', 'unclear', 'reading'];
const issueCode = (issue: string | null | undefined) => issue == null ? null : ISSUES.indexOf(issue);
// `reading` is the old name of `character` (the old site's migration 0055).
const issueName = (code: number | null) => code == null ? null : code === 6 ? 'character' : ISSUES[code] ?? null;
// A crop's state as the old site named it: a mark (seen, hard) is a pending crop that rounds made something of.
const stateName = (code: number) => code === STATE.checked ? 'checked' : code === STATE.flagged ? 'flagged' : 'pending';
const codePoints = (value: string) => [...value].map(c => 'U+' + c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')).join(' ');
const markable = (code: number) => code === STATE.pending || code === STATE.seen || code === STATE.hard;

// --- The crops a review reads ---

// What a crop holds now, as the old site's `units.data` held it: the bundle's record (or a corpus
// glyph's record from its pack) under the row's label, state and revision; the issue of its last event
// while it is flagged; the character a standing review named; the box a review redrew.
export type Current = { id: number; kind: number; char: number | null; label: string; state: number; revision: number; cut: number;
  quiz: number; data: Json; cutBox: Json | null; settled: number; logicalRevision: number; anchored: number | null; boundary: number | null;
  sourceId: string; pageRevision: number; record: Json };
// Whether a client that read the crop at `revision` saw it as it stands: at its revision now, or at one
// before marks only, which the old site's revision never counted (`settled`).
export const standing = (row: { revision: number; settled: number }, revision: unknown) =>
  Number.isSafeInteger(revision) && (revision as number) >= row.settled && (revision as number) <= row.revision;
// An event that only moved a mark: a round's seen or hard, or an undo of a round that took one away.
// The old site kept marks beside the crop and never moved its revision for them, so a crop's revision as
// the old site and the offline store count it leaves its marks out (`logicalRevision`).
export const MARKED = (e: string) => `(${e}.kind IN (3,4) OR (${e}.kind=5 AND coalesce((SELECT k.kind FROM events k WHERE k.id=${e}.reverses),3) IN (3,4)))`;
/** A crop's revision as the old site and the offline store count it. A publication that speaks for the
 *  crop records the store's revision and the crop's revision then (`review_revisions`); every event since
 *  but a mark or a publication's re-cut adds one. A crop no publication has spoken for since the cutover
 *  counts its revision less those. publish.py computes the same. */
// A publication's change to what a reviewer sees, as an event (publish.py `visible_change`): a new cut,
// character, state, kind, quiz or flags, or a crop put back. `published` names the columns it set, so an
// undo leaves them as it set them. A never-reviewed corpus glyph's follow moves its revision with no event
// (`crops.bumps`), since it has no review to protect.
export const PUBLISHED = 8;
// Events the store never counts: marks, and the publication's own (a re-cut clearing a mark, kind 7, and
// its changes, kind 8).
export const UNCOUNTED = (e: string) => `(${e}.kind IN (7,${PUBLISHED}) OR ${MARKED(e)})`;
// Only events up to the crop's revision count, so a statement that writes an event before moving the crop
// on (followCorpus) reads the same count on either side of it.
export const LOGICAL = (c: string) => `coalesce((SELECT a.revision+(SELECT count(*) FROM events e WHERE e.crop=a.crop AND e.revision>a.at
  AND e.revision<=${c}.revision AND NOT ${UNCOUNTED('e')}) FROM review_revisions a WHERE a.crop=${c}.id),
  ${c}.revision-${c}.bumps-(SELECT count(*) FROM events e WHERE e.crop=${c}.id AND e.revision<=${c}.revision AND ${UNCOUNTED('e')}))`;
// The crop's revision at the last publication that changed what a reviewer sees, or put it back: no view
// or review from before it stands, and nothing before it counts toward a view's standing.
const BOUNDARY = (c: string) => `(SELECT max(b.revision) FROM events b WHERE b.crop=${c}.id AND b.kind=${PUBLISHED})`;
export const reviewCropsQuery = () => `SELECT c.id,c.kind,c.char,g.text AS label,c.state,c.revision,c.cut,c.quiz,pg.rev,pg.key AS page_key,
  r.x,r.y,r.w,r.h,
  (SELECT coalesce(e.kept_issue,e.issue) FROM events e WHERE e.crop=c.id ORDER BY e.revision DESC LIMIT 1) AS issue,
  (SELECT n.text FROM events e JOIN graphemes n ON n.id=e.named WHERE e.crop=c.id AND e.named IS NOT NULL
    AND NOT EXISTS(SELECT 1 FROM events u WHERE u.reverses=e.id) ORDER BY e.revision DESC LIMIT 1) AS named,
  (SELECT max(e.revision) FROM events e WHERE e.crop=c.id AND NOT ${MARKED('e')} AND e.revision>coalesce(${BOUNDARY('c')},-1)) AS reviewed,
  (SELECT count(*) FROM events e WHERE e.crop=c.id AND ${MARKED('e')} AND e.revision>coalesce((SELECT max(f.revision) FROM events f
    WHERE f.crop=c.id AND NOT ${MARKED('f')} AND f.revision>coalesce(${BOUNDARY('c')},-1)),${BOUNDARY('c')},-1)) AS marks,
  ${BOUNDARY('c')} AS boundary,
  ${LOGICAL('c')} AS logical,(SELECT revision FROM review_revisions WHERE crop=c.id) AS anchored
  FROM json_each(?) j JOIN crops c ON c.id=j.value JOIN pages pg ON pg.id=c.id>>24
  LEFT JOIN graphemes g ON g.id=c.char LEFT JOIN reviews r ON r.crop=c.id`;
type CropRow = { id: number; kind: number; char: number | null; label: string | null; state: number; revision: number; cut: number; quiz: number;
  rev: number; x: number | null; y: number | null; w: number | null; h: number | null; issue: number | null; named: string | null;
  reviewed: number | null; marks: number; logical: number; anchored: number | null; boundary: number | null; page_key: string };

// A crop's page id as its record names it; a crop with none sits on its book's page (`book:<document>`).
export const pageId = (key: string): { page_id?: string } => key.startsWith('book:') ? {} : { page_id: key };
// Why a crop a write names is not there: a retired crop names the crop that replaced it.
async function missing(env: Env, id: number): Promise<Problem> {
  const named = await cropParam(env, publicId(id));
  if (named && 'moved' in named) return new Problem(404, 'This crop was replaced.', { replaced_by: publicId(named.moved) });
  return new Problem(404, 'This character is not in the published collection.');
}
/** The crops by id, each as it stands, or the 404 a write naming it answers; only the crops in
 *  `explained` are looked up for the crop that replaced them. Candidates are not reviewed here. */
export async function reviewCrops(env: Env, ids: number[], explained: number[] = ids): Promise<Map<number, Current | Problem>> {
  const found = new Map<number, Current | Problem>();
  if (!ids.length) return found;
  const rows = (await env.ATLAS.prepare(reviewCropsQuery()).bind(JSON.stringify([...new Set(ids)])).all<CropRow>()).results;
  // A crop a listing just showed has its entry at the edge, which spares reading its page's whole bundle.
  const entries = await cropEntries(env, rows);
  const pointer = (entry: Json) => ({ object: entry.record_place[0], offset: entry.record_place[1], size: entry.record_place[2] });
  const records = await readRecords(env, [...entries.values()].filter(entry => entry?.record_place).map(entry => pointer(entry!)));
  for (const row of rows) {
    const entry = entries.get(row.id);
    const record = entry?.record_place ? records.get(recordKey(pointer(entry))) : entry;
    if (!record || entry?.retired || row.kind === 2) continue;
    const { slot: _slot, record_place: _place, ...stored } = record;
    const label = row.label ?? '';
    const box = row.x == null ? null : { x: row.x, y: row.y, w: row.w, h: row.h };
    const data: Json = { ...stored, id: publicId(row.id), ...pageId(row.page_key), label, state: stateName(row.state), revision: row.revision,
      issue: row.state === STATE.flagged ? issueName(row.issue) : null,
      written_character: row.named ?? stored.written_character ?? null,
      ...(row.named ? { identity_status: 'assigned', identity_basis: 'human_review' } : {}),
      category: categoryOf(label), ...(box ? { box, box_pending: true } : {}) };
    // Marks since the last review that follow on from it, one revision each, leave the crop as a client saw it then.
    // From the last review after the crop's boundary (else the boundary), marks one revision each leave the
    // crop as a client saw it then; marks and events before the boundary never count.
    const base = row.reviewed ?? row.boundary ?? row.revision - row.marks;
    const settled = base + row.marks === row.revision ? base : row.revision;
    found.set(row.id, { id: row.id, kind: row.kind, char: row.char, label, state: row.state, revision: row.revision, cut: row.cut, quiz: row.quiz, data,
      cutBox: stored.box ?? null, settled, logicalRevision: row.logical, anchored: row.anchored, boundary: row.boundary, sourceId: entry!.id,
      pageRevision: row.rev, record: stored });
  }
  const gone = ids.filter(id => !found.has(id)), told = new Set(explained);
  const answers = await Promise.all(gone.map(id => told.has(id) ? missing(env, id) : new Problem(404, 'This character is not in the published collection.')));
  gone.forEach((id, i) => found.set(id, answers[i]));
  return found;
}

// A crop id as a write names it: its public id, or a text id from before the cutover, read through the
// alias map; null for one that names no crop.
async function cropNamed(env: Env, value: unknown, name: string): Promise<number | null> {
  text(value, 512, name, true);
  return await writtenCropField(env, value);
}

// --- Checks of what a review sends ---

// Whether a crop may be dealt in Quick review at all; its review state says whether it is due now.
// A local crop is withheld by the alignment repair, or when ainu-records measured almost no ink in its
// box (`geometry`, while the box is still the measured one). A corpus glyph needs an image this site may
// serve and a written character, since a round asks whether the crop is that character.
const blank = (data: Json) => Array.isArray(data.geometry?.flags) && data.geometry.flags.includes('sparse-ink')
  && ['x', 'y', 'w', 'h'].every(k => data.geometry.box?.[k] === data.box?.[k]);
export const dealable = (origin: string, data: Json) => origin === 'corpus' ? Boolean(data.proxyable && data.written_character) : data.repair?.quiz !== false && !blank(data);
// The page rectangle a reviewer may redraw a local crop's box in, in page pixels: the context the
// inspector shows, which lies inside the page. A corpus glyph's box belongs to its source, so it has none.
export function redrawLimits(data: Json): { x: number; y: number; w: number; h: number } | null {
  const c = data?.context_box, s = data?.source_scale;
  if (!data?.context || !c || !data.crop_box || !Array.isArray(s) || !(s[0] > 0) || !(s[1] > 0)) return null;
  return { x: Math.floor(c.x / s[0]), y: Math.floor(c.y / s[1]), w: Math.ceil((c.x + c.w) / s[0]) - Math.floor(c.x / s[0]), h: Math.ceil((c.y + c.h) / s[1]) - Math.floor(c.y / s[1]) };
}
// A redrawn box as the inspector sends it: whole page pixels, at least two each way, inside the page view.
export function redrawnBox(value: Json, data: Json): { x: number; y: number; w: number; h: number } {
  const limits = redrawLimits(data);
  if (!limits) throw new Problem(422, 'This crop cannot be redrawn.');
  if (!value || typeof value !== 'object' || Object.keys(value).sort().join() !== 'h,w,x,y' || !['x', 'y', 'w', 'h'].every(k => Number.isSafeInteger(value[k])))
    throw new Problem(422, 'A box is four whole pixels: x, y, w, h.');
  const { x, y, w, h } = value;
  if (w < 2 || h < 2 || x < limits.x || y < limits.y || x + w > limits.x + limits.w || y + h > limits.y + limits.h)
    throw new Problem(422, 'The crop must stay inside the source image.');
  return { x, y, w, h };
}
// What a shown crop's pixels are named by: a local crop's page hash, a corpus glyph's source revision.
const pixels = (crop: Json) => crop.image_sha256 ?? crop.source_revision;
// The crops a round names: flagged answers, and crops it showed and left unflagged. A round carries
// either or both; a single-crop review carries only its answer.
export function validRound(input: Json, target?: string): { answers: Json[]; seen: Json[]; skipped: Json[] } {
  const round = !target;
  if (!round && (input.seen !== undefined || input.skipped !== undefined)) throw new Problem(422, 'Only a round records seen or skipped crops.');
  const answers = round ? (input.answers ?? []) : [{ ...input, id: target }];
  const seen = round ? (input.seen ?? []) : [];
  const skipped = round ? (input.skipped ?? []) : [];
  if (!Array.isArray(answers) || !Array.isArray(seen) || !Array.isArray(skipped)) throw new Problem(422, `A round needs 1–${ROUND_MAX} distinct crops.`);
  const ids = [...answers, ...seen, ...skipped].map(crop => crop?.id);
  if (ids.length < 1 || ids.length > ROUND_MAX || new Set(ids).size !== ids.length) throw new Problem(422, `A round needs 1–${ROUND_MAX} distinct crops.`);
  for (const crop of [...seen, ...skipped]) {
    text(crop?.id, 512, 'character id', true);
    if (typeof pixels(crop) !== 'string' || !/^[a-f0-9]{64}$/.test(pixels(crop))) throw new Problem(422, 'Invalid image hash.');
    // A crop's image is compared with its record, never stored. Corpus glyphs link their source's own
    // file, and an HNG file name percent-encodes Japanese, so the address can run past 256 characters.
    if (crop.image !== undefined) text(crop.image, 2048, 'crop image', true);
  }
  return { answers, seen, skipped };
}
// A batch correction names one written character and the crops a reader selected as that character:
// each crop becomes a `wrong`/`character` answer, checked against its own revision and pixels.
// A line's correction (`line: true`) names a character for each crop instead, or `issue: 'blank'` for
// a box that is no character; a crop that changed or was checked meanwhile is skipped and reported.
export function validBatch(input: Json): { character: string | null; line: boolean; crops: Json[] } {
  if (input.line !== undefined && typeof input.line !== 'boolean') throw new Problem(422, 'A line correction is true or absent.');
  const line = input.line === true;
  if (line && input.character !== undefined) throw new Problem(422, 'A line correction names a character for each crop.');
  const character = line ? null : writtenCharacter(text(input.character, 32, 'character', true)!);
  const crops = input.crops;
  if (!Array.isArray(crops) || crops.length < 1 || crops.length > ROUND_MAX || new Set(crops.map(crop => crop?.id)).size !== crops.length)
    throw new Problem(422, `A correction needs 1–${ROUND_MAX} distinct crops.`);
  for (const crop of crops) {
    text(crop?.id, 512, 'crop id', true);
    // A batch names a character only; a crop is redrawn one at a time, in its own review. A line's crop may
    // carry its redrawn box beside its character: the box is checked against the crop itself later.
    if (crop.box !== undefined && (!line || crop.issue !== undefined || crop.box === null || typeof crop.box !== 'object')) throw new Problem(422, 'A correction of many crops cannot redraw one.');
    if (!line && (crop.character !== undefined || crop.issue !== undefined)) throw new Problem(422, 'Only a line correction names a character for each crop.');
    if (line) {
      if (crop.issue !== undefined && (crop.issue !== 'blank' || crop.character !== undefined)) throw new Problem(422, 'A box is marked as no character by itself.');
      if (crop.issue === undefined) crop.character = writtenCharacter(text(crop.character, 32, 'character', true)!);
    }
  }
  return { character, line, crops };
}
// What a batch does to each crop it names: the crops to write, the crops refused (changed since the
// reader loaded them, without an image to judge, missing, or already checked as another character) and
// the crops already written as the character, which need no write. A one-character correction refuses
// as a whole; a line's skips them (`validBatch`).
export function judgeBatch(batch: { character: string | null; line: boolean }, answers: Json[], rows: Map<string, Current | Problem>) {
  const refused: Json[] = [], chosen: Json[] = [], unchanged: string[] = [];
  for (const crop of answers) {
    const row = rows.get(crop.id)!;
    if (row instanceof Problem) { refused.push({ id: crop.id, reason: 'missing' }); continue }
    const data = row.data, glyph = row.kind === 1;
    if (!standing(row, crop.revision) || (glyph ? crop.source_revision !== data.source_revision : crop.image_sha256 !== data.image_sha256)) { refused.push({ id: crop.id, reason: 'changed' }); continue }
    if (glyph && !data.proxyable) { refused.push({ id: crop.id, reason: 'unavailable' }); continue }
    if (crop.issue === 'blank') {
      if (data.state === 'checked') refused.push({ id: crop.id, reason: 'checked' });
      else chosen.push({ ...crop, verdict: 'wrong', issue: 'blank' });
      continue;
    }
    const character = crop.character ?? batch.character;
    const identity = glyph ? data.written_character : (data.written_character || data.label);
    if (identity === character) {
      if (data.state === 'checked') unchanged.push(crop.id);
      else chosen.push({ id: crop.id, revision: crop.revision, image_sha256: crop.image_sha256, source_revision: crop.source_revision, verdict: 'match', ...(crop.box ? { box: crop.box } : {}) });
    } else if (data.state === 'checked') refused.push({ id: crop.id, reason: 'checked' });
    else chosen.push({ ...crop, verdict: 'wrong', issue: 'character', character });
  }
  return { chosen, refused, unchanged };
}
function validateAnswer(answer: Json, row: Current, round: boolean, corpus = false, batch = false, line = false) {
  const current = row.data;
  if (!standing(row, answer.revision)) throw new Problem(409, 'This character changed. Reload it.');
  if (corpus ? answer.source_revision !== current.source_revision : answer.image_sha256 !== current.image_sha256) throw new Problem(409, 'The source image changed. Reload it.');
  if (!['match', 'wrong', 'unsure'].includes(answer.verdict)) throw new Problem(422, 'Choose a review decision.');
  if (answer.issue != null && !['character', 'merged', 'crop', 'blank', 'other', 'unclear'].includes(answer.issue)) throw new Problem(422, 'Unknown issue.');
  if (answer.verdict === 'match' && (answer.character || answer.correction || answer.issue))
    throw new Problem(422, 'A matching crop cannot also have an issue.');
  if (answer.verdict === 'wrong' && !answer.issue) throw new Problem(422, 'Choose an issue.');
  for (const field of ['character', 'correction']) text(answer[field], 32, field);
  // Typed characters describe a joined crop; one character is named as `character`.
  if (answer.correction && answer.issue !== 'merged') throw new Problem(422, 'Typed characters belong to a joined-character issue.');
  text(answer.note, 2000, 'note');
  if (answer.character) writtenCharacter(answer.character);
  if (answer.verdict === 'wrong' && answer.character && literal(answer.character) === current.written_character)
    throw new Problem(422, 'Choose a different character or a different issue.');
  if (answer.box !== undefined) {
    // A redrawn box is the crop's fix: saved as a match on the pixels it names, never in a round. A line's
    // crop may also be relabelled in the same save.
    if (round || corpus || (batch && !line)) throw new Problem(422, 'This crop cannot be redrawn here.');
    const fixed = answer.verdict === 'match' && answer.issue == null && !answer.character && !answer.correction;
    const relabelled = line && answer.verdict === 'wrong' && answer.issue === 'character' && answer.character && !answer.correction;
    if (!fixed && !relabelled) throw new Problem(422, 'A redrawn crop is saved as fixed.');
    answer.box = redrawnBox(answer.box, current);
  }
}

// The characters a grapheme is written as, by the grapheme table: は's family is は, ハ and its
// hentaigana. A key that names no family (ツ + U+309A, or a character the table lacks) is its own text
// alone; a key that names a family's member rather than the family is refused. Two index seeks.
export const roundMembersQuery = () => `SELECT g.id,g.head,h.text AS head_text,
  (SELECT json_group_array(m.text) FROM graphemes m WHERE m.head=g.id) AS members
  FROM graphemes g LEFT JOIN graphemes h ON h.id=g.head WHERE g.text=?`;
export async function roundMembers(env: Env, key: string): Promise<string[]> {
  const own = compose(graphemeText(key));
  const row = await env.ATLAS.prepare(roundMembersQuery()).bind(own).first<{ id: number; head: number | null; head_text: string | null; members: string }>();
  if (row && row.head != null && row.head !== row.id) throw new Problem(422, `${graphemeText(key)} is filed under ${row.head_text}.`);
  const members = row ? JSON.parse(row.members) as string[] : [];
  return members.length ? members : [own];
}

// --- The statements of a change ---

// Who writes: the actor a user writes under, their own id, made on first use.
const actorOf = (env: Env, user: string) => env.ATLAS.prepare('SELECT id FROM actors WHERE key=?').bind(user).first<{ id: number }>();
/** The user's actor, made on their first write. `known` is a lookup already under way. */
export async function actorFor(env: Env, user: string, known = actorOf(env, user)): Promise<number> {
  const read = () => actorOf(env, user);
  const found = await known;
  if (found) return found.id;
  await env.ATLAS.prepare("INSERT OR IGNORE INTO actors(key,user,via,at) VALUES(?,?,'account',?)").bind(user, user, Date.now()).run();
  return (await read())!.id;
}

// A crop's mark at its current cut, by the old triggers' rules: hard once two reviewers skipped it in
// rounds not undone, seen once such a round showed it and left it unflagged, else none (pending).
const MARK = (c: string) => `CASE WHEN (SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
  WHERE k.crop=${c}.id AND k.cut=${c}.cut)>=2 THEN ${STATE.hard}
  WHEN EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0 WHERE s.crop=${c}.id AND s.cut=${c}.cut) THEN ${STATE.seen}
  ELSE ${STATE.pending} END`;
const GEN = '(SELECT gen FROM generation WHERE id=0)';
const SUBMISSION = '(SELECT id FROM submissions WHERE key=?)';
// What the crop held before an event: its state, its last event's issue and its redrawn box.
const WAS = `c.state,(SELECT coalesce(p.kept_issue,p.issue) FROM events p WHERE p.crop=c.id ORDER BY p.revision DESC LIMIT 1),r.x,r.y,r.w,r.h`;
const EVENT_COLUMNS = 'crop,revision,cut,gen,actor,submission,at,kind,label,state,quiz,x,y,w,h,note,issue,kept_issue,text,shown,named,reverses,was_state,was_issue,was_x,was_y,was_w,was_h';

type Box = { x: number; y: number; w: number; h: number };
/** One crop's change: the event's values, which the crop takes. `expected` and `cut` are what the
 *  writer read; a crop changed since aborts the batch. `label` and `named` are characters (a missing
 *  one joins `graphemes`); `mark` sets the state to the crop's mark when the event leaves it pending;
 *  `kept_issue` is the issue the crop stays flagged with when it is not `issue`; `shown` defaults to the crop's label before; `category` (a key) and `box` keep the crop's when
 *  undefined (`box` null clears the redrawn box); `quiz` null keeps it. */
export type CropChange = { crop: number; expected: number; cut: number; kind: number; label: string | null; state: number; mark?: boolean;
  issue: number | null; kept_issue?: number | null; text?: string | null; note?: string | null; named?: string | null; shown?: number | null; quiz?: number | null;
  category?: string; box?: Box | null; reverses?: number | null; pageRevision?: number };

// The events of a list of changes, each guarded by the revision and cut its writer read.
function eventStatements(env: Env, actor: number, key: string, at: number, changes: CropChange[]): D1PreparedStatement[] {
  const list = JSON.stringify(changes.map(c => ({ ...c, box: undefined, x: c.box?.x, y: c.box?.y, w: c.box?.w, h: c.box?.h,
    mark: c.mark ? 1 : 0, ...(c.shown === undefined ? {} : { shown: c.shown, shown_set: 1 }) })));
  const characters = [...new Set(changes.flatMap(c => [c.label, c.named]).filter((c): c is string => Boolean(c)))];
  const categories = [...new Set(changes.map(c => c.category).filter((c): c is string => c !== undefined))];
  const v = (field: string) => `j.value->>'${field}'`;
  return [
    ...(characters.length ? [env.ATLAS.prepare('INSERT OR IGNORE INTO graphemes(text) SELECT value FROM json_each(?)').bind(JSON.stringify(characters))] : []),
    // A category a label is the first of gets its code.
    ...categories.map(category => env.ATLAS.prepare(`INSERT OR IGNORE INTO codes(field,code,key) SELECT 'category',coalesce(max(code),0)+1,?
      FROM codes WHERE field='category'`).bind(category)),
    env.ATLAS.prepare(`INSERT INTO events(${EVENT_COLUMNS})
      SELECT c.id,(SELECT x.revision+1 FROM crops x WHERE x.id=c.id AND x.revision=${v('expected')} AND x.cut=${v('cut')}
        AND (${v('pageRevision')} IS NULL OR (SELECT rev FROM pages WHERE id=c.id>>24)=${v('pageRevision')})),c.cut,${GEN},?,${SUBMISSION},?,${v('kind')},
      (SELECT id FROM graphemes WHERE text=${v('label')}),CASE WHEN ${v('mark')}=1 AND ${v('state')} IN (0,3,4) THEN ${MARK('c')} ELSE ${v('state')} END,
      ${v('quiz')},${v('x')},${v('y')},${v('w')},${v('h')},${v('note')},${v('issue')},${v('kept_issue')},${v('text')},
      CASE WHEN ${v('shown_set')}=1 THEN ${v('shown')} ELSE c.char END,(SELECT id FROM graphemes WHERE text=${v('named')}),${v('reverses')},${WAS}
      FROM json_each(?) j LEFT JOIN crops c ON c.id=${v('crop')} LEFT JOIN reviews r ON r.crop=c.id ORDER BY j.key`).bind(actor, key, at, list),
  ];
}

// The crops' rows, counts and overlay, from the events this write added (those of its submission at
// its time, of its kinds), each read at the revision the event moved its crop to. `list` holds the
// changes' categories and boxes; a crop it does not name keeps both.
function applyStatements(env: Env, key: string, at: number, kinds: number[], changes: CropChange[]): D1PreparedStatement[] {
  const list = JSON.stringify(changes.map(c => ({ crop: c.crop, category: c.category, box: c.box === undefined ? 0 : 1 })));
  // ?1 the changes, ?2 the submission's key, ?3 the time.
  const mine = `e.submission=(SELECT id FROM submissions WHERE key=?2) AND e.at=?3 AND e.kind IN (${kinds.map(Number).join(',')})`;
  const cell = 'c.kind,coalesce(c.char,-1),coalesce(c.family,-1),c.book,coalesce(c.production,-1),coalesce(c.style,-1),c.state,c.quiz,coalesce((c.year-CASE WHEN c.year<0 THEN 9 ELSE 0 END)/10*10,-1)';
  const named = (field: string) => `(SELECT j.value->>'${field}' FROM json_each(?1) j WHERE j.value->>'crop'=c.id)`;
  const box = (k: string) => `iif(coalesce(${named('box')},0)=1,e.${k},r.${k})`;
  const tally = (sign: string, step: string) => env.ATLAS.prepare(`INSERT INTO tally(gen,kind,char,family,book,production,style,state,quiz,decade,n)
    SELECT ${GEN},${cell},${sign}count(*) FROM events e JOIN crops c ON c.id=e.crop AND c.revision${step}=e.revision WHERE ${mine.replaceAll('?2', '?1').replaceAll('?3', '?2')}
    GROUP BY ${cell} ON CONFLICT DO UPDATE SET n=n+excluded.n`).bind(key, at);
  return [
    tally('-', '+1'),
    env.ATLAS.prepare(`INSERT INTO reviews(crop,gen,at,char,state,style,quiz,x,y,w,h,base_char,base_state,base_style,base_quiz)
      SELECT c.id,${GEN},e.at,e.label,e.state,c.style,coalesce(e.quiz,c.quiz),${box('x')},${box('y')},${box('w')},${box('h')},c.char,c.state,c.style,c.quiz
      FROM events e JOIN crops c ON c.id=e.crop AND c.revision+1=e.revision LEFT JOIN reviews r ON r.crop=c.id WHERE ${mine}
      ON CONFLICT(crop) DO UPDATE SET gen=excluded.gen,at=excluded.at,char=excluded.char,state=excluded.state,quiz=excluded.quiz,
      x=excluded.x,y=excluded.y,w=excluded.w,h=excluded.h`).bind(list, key, at),
    env.ATLAS.prepare(`UPDATE crops AS c SET (char,state,quiz,category,revision)=(SELECT e.label,e.state,coalesce(e.quiz,c.quiz),
      coalesce((SELECT code FROM codes WHERE field='category' AND key=${named('category')}),c.category),e.revision
      FROM events e WHERE e.crop=c.id AND e.revision=c.revision+1)
      WHERE c.id IN (SELECT e.crop FROM events e WHERE ${mine}) AND EXISTS(SELECT 1 FROM events e WHERE e.crop=c.id AND e.revision=c.revision+1)`).bind(list, key, at),
    tally('', ''),
  ];
}

/** The statements of a list of crop changes made in the submission `key` (inserted earlier in the
 *  batch), in order: missing characters, the events, the counts out of the old cells, the overlay, the
 *  crops' rows, the counts into the new cells. */
export function cropChanges(env: Env, actor: number, key: string, at: number, changes: CropChange[]): D1PreparedStatement[] {
  if (!changes.length) return [];
  return [...eventStatements(env, actor, key, at, changes), ...applyStatements(env, key, at, [...new Set(changes.map(c => c.kind))], changes)];
}
// Mark events for crops a round showed or skipped, or a round's undo stopped counting: a crop a review
// left pending (pending, seen or hard) takes its mark when that changed. A round's own event is of the
// mark's kind; an undo's names the round's mark event on the crop, if it made one.
function markStatement(env: Env, actor: number, key: string, at: number, crops: number[], undo: boolean): D1PreparedStatement {
  const submission = '(SELECT id FROM submissions WHERE key=?2)';
  const accepted = `EXISTS(SELECT 1 FROM seen s WHERE s.submission=${submission} AND s.crop=m.id)
      OR EXISTS(SELECT 1 FROM skips s WHERE s.submission=${submission} AND s.crop=m.id)`;
  const reverses = undo ? `(SELECT p.id FROM events p WHERE p.crop=m.id AND p.submission=${submission} AND p.kind IN (3,4) ORDER BY p.revision DESC LIMIT 1)` : 'NULL';
  return env.ATLAS.prepare(`INSERT INTO events(${EVENT_COLUMNS})
    SELECT id,revision+1,cut,${GEN},?1,${submission},?3,${undo ? KIND.undo : 'mark'},char,mark,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,char,NULL,${reverses},
    state,was_issue,rx,ry,rw,rh
    FROM (SELECT c.*,${MARK('c')} AS mark,(SELECT coalesce(p.kept_issue,p.issue) FROM events p WHERE p.crop=c.id ORDER BY p.revision DESC LIMIT 1) AS was_issue,
      r.x AS rx,r.y AS ry,r.w AS rw,r.h AS rh
      FROM json_each(?4) j JOIN crops c ON c.id=j.value LEFT JOIN reviews r ON r.crop=c.id ORDER BY j.key) m
    WHERE state IN (0,3,4) AND mark<>state${undo ? '' : ` AND mark>0 AND (${accepted})`}`).bind(actor, key, at, JSON.stringify(crops));
}
/** A reviewer's totals: verdicts and submissions that stand, and when they last saved. */
export function actorStats(env: Env, actor: number, reviews: number, rounds: number, at: number): D1PreparedStatement {
  return env.ATLAS.prepare(`INSERT INTO actor_stats(actor,reviews,rounds,at) VALUES(?,?,?,?)
    ON CONFLICT(actor) DO UPDATE SET reviews=coalesce(reviews,0)+excluded.reviews,rounds=coalesce(rounds,0)+excluded.rounds,
    at=max(coalesce(at,0),excluded.at)`).bind(actor, reviews, rounds, at);
}
// A corpus glyph's first write, in the order the old site gave its `units` row.
function corpusRows(env: Env, crops: number[], verdicts: number[], key: string): D1PreparedStatement[] {
  return crops.length ? [env.ATLAS.prepare(`INSERT OR IGNORE INTO corpus_rows(crop,seq)
    SELECT c.id,(SELECT coalesce(max(seq),0) FROM corpus_rows)+j.key+1 FROM json_each(?1) j JOIN crops c ON c.id=j.value AND c.kind=1
    WHERE c.id IN (SELECT value FROM json_each(?2))
      OR EXISTS(SELECT 1 FROM seen s WHERE s.submission=(SELECT id FROM submissions WHERE key=?3) AND s.crop=c.id)
      OR EXISTS(SELECT 1 FROM skips s WHERE s.submission=(SELECT id FROM submissions WHERE key=?3) AND s.crop=c.id)
    ORDER BY j.key`).bind(JSON.stringify(crops), JSON.stringify(verdicts), key)] : [];
}
// A batch that failed on a crop's revision: another write moved it first.
const conflict = (error: unknown) => /NOT NULL constraint failed: events\.revision|UNIQUE constraint failed: events\.crop/.test(String(error));
// Whether every crop the changes name moved since it was read only by marks, one revision each, at
// the cut it was read at: the old site never counted marks, so the write is made again on a fresh read.
export const marksOnlyQuery = () => `SELECT count(*) AS n FROM json_each(?) j JOIN crops c ON c.id=j.value->>'crop'
  WHERE (j.value->>'pageRevision' IS NOT NULL AND (SELECT rev FROM pages WHERE id=c.id>>24) IS NOT j.value->>'pageRevision')
  OR c.cut<>j.value->>'cut' OR c.revision<>j.value->>'expected'+(SELECT count(*) FROM events e WHERE e.crop=c.id AND e.revision>j.value->>'expected')
  OR EXISTS(SELECT 1 FROM events e WHERE e.crop=c.id AND e.revision>j.value->>'expected' AND NOT ${MARKED('e')})`;
// The 409 of a save that lost its guard: its page was published again, or another write changed the crop.
async function conflictProblem(env: Env, changes: CropChange[]): Promise<Problem> {
  const paged = changes.filter(c => c.pageRevision !== undefined);
  const republished = paged.length ? (await env.ATLAS.prepare(`SELECT count(*) AS n FROM json_each(?) j
    JOIN pages p ON p.id=(j.value->>'crop')>>24 WHERE p.rev<>j.value->>'pageRevision'`)
    .bind(JSON.stringify(paged.map(c => ({ crop: c.crop, pageRevision: c.pageRevision })))).first<{ n: number }>())?.n : 0;
  return republished ? new Problem(409, "This crop's page was published again. Reload it.")
    : new Problem(409, 'Another review changed this crop. Reload it.');
}
async function marksOnly(env: Env, changes: CropChange[]): Promise<boolean> {
  const list = JSON.stringify(changes.map(c => ({ crop: c.crop, expected: c.expected, cut: c.cut,
    pageRevision: c.pageRevision })));
  return (await env.ATLAS.prepare(marksOnlyQuery()).bind(list).first<{ n: number }>())?.n === 0;
}

// --- Submitting ---

/** Save a review: a round (no target), a batch correction (`@batch`), a corpus glyph's review
 *  (`@corpus`) or one crop's review (its id as written in the path). */
export async function submit(env: Env, request: Request, user: string, target?: string) {
  const sent = await body(request);
  return await save(env, sent, request.headers.get('cf-connecting-ip'), user, target, true);
}
async function save(env: Env, sent: Json, address: string | null, user: string, target: string | undefined, again: boolean): Promise<Json> {
  // The checks below normalise what they read in place; a second attempt starts from what was sent.
  const input = structuredClone(sent);
  const mode = target;
  const corpus = target === '@corpus', batch = target === '@batch';
  if (corpus) target = text(input.identity, 512, 'corpus identity', true)!;
  if (batch) target = undefined;
  const id = text(input.id, 64, 'submission id', true)!;
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw new Problem(422, 'Invalid submission id.');
  // A retry is the same submission whatever else came on screen meanwhile: the seen crops are left
  // out of the signature, as the local server compares only the answers, and the first result stands.
  const { seen: _, skipped: __, ...signed } = input;
  const signature = canonical({ target: batch ? '@batch' : target || null, input: signed });
  const key = user + ':' + id;
  const previous = () => env.ATLAS.prepare('SELECT request,response FROM submissions WHERE key=?').bind(key).first<{ request: string; response: string }>();
  // A corpus glyph's inspector shows the glyph it gets back, with the form its ledger holds now.
  const answered = async (stored: Json) => corpus ? await corpusAnswer(env, stored) : stored;
  const before = await previous();
  if (before) { if (before.request !== signature) throw new Problem(409, 'This submission was already saved with different answers.'); return answered(JSON.parse(before.response)) }
  const round = !target && !batch;
  // A round names its grapheme; each crop it answers or saw is one of the grapheme's characters.
  const grapheme = round ? graphemeKey(input.grapheme) : null;
  if (round && !grapheme) throw new Problem(422, 'A round names its grapheme.');
  const members = grapheme ? await roundMembers(env, grapheme) : null;
  if (batch && (input.seen !== undefined || input.skipped !== undefined)) throw new Problem(422, 'Only a round records seen or skipped crops.');
  const correction = batch ? validBatch(input) : null;
  const { answers, seen, skipped } = batch ? { answers: correction!.crops, seen: [], skipped: [] } : validRound(input, target);
  // A redrawn box is held to the rate batch corrections are, by address.
  if (again && !batch && answers.some(answer => answer.box !== undefined)) {
    const { success } = await env.CORRECTIONS.limit({ key: addressKey(address) });
    if (!success) throw new Problem(429, 'Too many corrections at once. Wait a minute and try again.');
  }
  // Every crop the request names, by its public id or an old text id.
  const listed = [...answers, ...seen, ...skipped].map(crop => crop?.id);
  const named = new Map<string, number | null>(await Promise.all(listed.map(async value => [value, await cropNamed(env, value, 'character id')] as const)));
  const read = await reviewCrops(env, [...named.values()].filter((n): n is number => n !== null),
    answers.map(answer => named.get(answer.id)).filter((n): n is number => n != null));
  const notFound = new Problem(404, 'This character is not in the published collection.');
  const rows = new Map(answers.map(answer => { const n = named.get(answer.id); return [answer.id as string, n == null ? notFound : read.get(n)!] }));
  // A correction names crops a reader selected on the grid, so each is judged here: one that changed
  // since the page loaded, one whose image cannot be shown, or one a person already checked as another
  // character is refused (all are named, and nothing is saved); one already written as the character
  // is confirmed, unless a person already checked it, when it is left as it is.
  let unchanged: string[] = [];
  const skippedCrops: Json[] = [];
  if (batch) {
    const judged = judgeBatch(correction!, answers, rows);
    // A line's correction saves the crops that still stand and reports the rest; a crop that changed is never overwritten.
    if (judged.refused.length && !correction!.line) throw new Problem(409, 'Some of these crops changed or were already checked. Reload them.', { targets: judged.refused });
    skippedCrops.push(...judged.refused);
    unchanged = judged.unchanged;
    answers.splice(0, answers.length, ...judged.chosen);
  }
  const at = Date.now();
  const changes: CropChange[] = [], results: Json[] = [], captured: ReviewedInput[] = [];
  const first: number[] = [];
  for (const answer of answers) {
    const found = rows.get(answer.id)!;
    if (found instanceof Problem) throw found;
    const row = found, current = row.data, glyph = row.kind === 1;
    validateAnswer(answer, row, round, glyph, batch, Boolean(correction?.line));
    if (glyph && (!current.proxyable || (current.identity_status === 'unassigned' && answer.verdict === 'match')))
      throw new Problem(422, 'Choose a written character or report an issue.');
    if (round && (!row.quiz || !members!.includes(current.label))) throw new Problem(409, 'This round changed. Reload it.');
    const written = answer.character ? literal(answer.character) : null;
    // A batch names the character only: a crop reported for its box, a blank or a merge stays reported.
    const kept = batch && current.state === 'flagged' && current.issue && current.issue !== 'character' ? current.issue : null;
    const resolved = !kept && (answer.verdict === 'match' || Boolean(answer.issue === 'character' && written));
    changes.push({ crop: row.id, expected: row.revision, cut: row.cut, 
      ...(row.kind === 0 ? { pageRevision: row.pageRevision } : {}), kind: KIND[answer.verdict as 'match'], label: written ?? (row.char === null ? null : row.label),
      state: resolved ? STATE.checked : STATE.flagged, issue: issueCode(answer.issue ?? null), ...(kept ? { kept_issue: issueCode(kept) } : {}), text: answer.correction || null, note: answer.note || null,
      named: written, ...(written ? { category: categoryOf(written) } : {}), ...(answer.box ? { box: answer.box } : {}) });
    results.push({ target_id: publicId(row.id), revision: row.revision + 1, state: resolved ? 'checked' : 'flagged' });
    first.push(row.id);
    captured.push({ row, change: changes[changes.length - 1], answer, kept });
  }
  // A crop that left the queue, changed its pixels or moved to another character since the round
  // was dealt was not seen as it stands, so it is skipped rather than failing the round.
  const stands = (crop: Json) => {
    const n = named.get(crop.id), row = n == null ? null : read.get(n);
    if (!row || row instanceof Problem || !row.quiz) return null;
    const data = row.data;
    // `image_sha256` names the page here, so a crop re-cut on the same page would still match it;
    // the crop's own image is what the reader saw. A client that does not send it is held to the rest.
    // A corpus glyph's source revision covers its box and image reference.
    const same = row.kind === 1 ? data.source_revision === crop.source_revision : data.image_sha256 === crop.image_sha256;
    // A view from before the crop's last boundary, or since moved past by a review, records nothing for it;
    // so does one that names no revision of a crop with a boundary, which an older client sends.
    if (crop.revision === undefined ? row.boundary !== null : !standing(row, crop.revision)) return null;
    return same && members!.includes(data.label) && (crop.image === undefined || crop.image === data.image) ? row.id : null;
  };
  const shown = seen.map(stands).filter((n): n is number => n !== null);
  const passed = skipped.map(stands).filter((n): n is number => n !== null);
  const result = batch ? { id, results, unchanged, ...(correction!.line ? { skipped: skippedCrops } : {}) }
    : { id, results: [...results.map(r => ({ id: null, target_id: r.target_id, field: 'review', revision: r.revision, state: r.state })),
      ...shown.map(crop => ({ target_id: publicId(crop), field: 'seen' })), ...passed.map(crop => ({ target_id: publicId(crop), field: 'skip' }))] };
  // Observations must still name the cut and character seen during validation when the batch commits.
  const observations = (ids: number[]) => JSON.stringify(ids.map(id => {
    const row = read.get(id) as Current;
    return { crop: id, cut: row.cut, char: row.char, revision: row.revision, target_id: publicId(id) };
  }));
  const shownRows = observations(shown), passedRows = observations(passed);
  // At the revision validation read: a publication or a write in between leaves the crop unrecorded.
  const observed = `c.id=j.value->>'crop' AND c.cut=j.value->>'cut' AND c.char IS j.value->>'char' AND c.revision=j.value->>'revision' AND c.quiz=1 AND c.kind<2`;
  // The reviewer's actor is looked up while the evidence is read and stored; a new one is made only after.
  const reviewer = actorOf(env, user);
  reviewer.catch(() => {});
  // A crop a publication anchored takes its baseline from its bundle record alone; only the others read history.
  const priors = await priorBaselines(env, captured.filter(c => c.row.kind === 0 && c.row.anchored === null).map(c => c.row.id));
  const evidence = await captureReviews(env, captured, sent, mode, user, at, priors);
  const actor = await actorFor(env, user, reviewer);
  const statements = [env.ATLAS.prepare('INSERT INTO submissions(key,actor,request,response,at,evidence) VALUES (?,?,?,?,?,?)').bind(key, actor, signature, JSON.stringify(result), at, evidence),
    ...eventStatements(env, actor, key, at, changes)];
  if (shown.length) statements.push(env.ATLAS.prepare(`INSERT INTO seen(submission,crop,cut,at)
    SELECT ${SUBMISSION},c.id,c.cut,? FROM json_each(?) j JOIN crops c ON ${observed}`).bind(key, at, shownRows));
  // One skip per reviewer and crop: the newest.
  if (passed.length) statements.push(env.ATLAS.prepare(`INSERT INTO skips(crop,actor,cut,at,submission)
    SELECT c.id,?,c.cut,?,${SUBMISSION} FROM json_each(?) j JOIN crops c ON ${observed} WHERE true
    ON CONFLICT(crop,actor) DO UPDATE SET cut=excluded.cut,at=excluded.at,submission=excluded.submission`).bind(actor, at, key, passedRows));
  statements.push(...corpusRows(env, [...first, ...shown, ...passed], first, key));
  if (shown.length || passed.length) statements.push(markStatement(env, actor, key, at, [...shown, ...passed], false));
  statements.push(...applyStatements(env, key, at, [...VERDICTS, KIND.seen, KIND.hard], changes));
  statements.push(actorStats(env, actor, changes.length, 1, at));
  if (shown.length || passed.length) statements.push(env.ATLAS.prepare(`UPDATE submissions SET response=json_set(response,'$.results',json((
    SELECT json_group_array(json(value)) FROM (
      SELECT value FROM json_each(?2)
      UNION ALL SELECT json_object('target_id',j.value->>'target_id','field','seen') AS value
        FROM json_each(?3) j JOIN seen s ON s.crop=j.value->>'crop' AND s.submission=(SELECT id FROM submissions WHERE key=?1)
      UNION ALL SELECT json_object('target_id',j.value->>'target_id','field','skip') AS value
        FROM json_each(?4) j JOIN skips s ON s.crop=j.value->>'crop' AND s.submission=(SELECT id FROM submissions WHERE key=?1)
    ) m))) WHERE key=?1`).bind(key, JSON.stringify(result.results.filter(r => r.field !== 'seen' && r.field !== 'skip')), shownRows, passedRows));
  // The response names each review's event, which the batch numbers.
  if (!batch && changes.length) statements.push(env.ATLAS.prepare(`UPDATE submissions SET response=json_set(response,${changes.map((c, i) =>
    `'$.results[${i}].id',(SELECT e.id FROM events e WHERE e.submission=submissions.id AND e.crop=${Number(c.crop)} AND e.kind IN (0,1,2))`).join(',')}) WHERE key=?`).bind(key));
  statements.push(env.ATLAS.prepare('SELECT response FROM submissions WHERE key=?').bind(key));
  let saved: Json;
  try {
    const out = await env.ATLAS.batch<{ response: string }>(statements);
    saved = JSON.parse(out[out.length - 1].results[0].response);
  } catch (error) {
    const repeat = await previous();
    if (repeat?.request === signature) return answered(JSON.parse(repeat.response));
    if (conflict(error) && again && await marksOnly(env, changes)) return await save(env, sent, address, user, mode, false);
    if (conflict(error)) throw await conflictProblem(env, changes);
    throw error;
  }
  return answered(saved);
}
// A corpus glyph's review answers with the glyph as listings now show it, its form, and its event.
async function corpusAnswer(env: Env, stored: Json): Promise<Json> {
  const saved = stored.results[0];
  const crop = await writtenCropField(env, saved.target_id);
  const [current] = crop === null ? [] : [(await reviewCrops(env, [crop])).get(crop)];
  const [item] = crop === null ? [] : await cropItems(env, [crop]);
  if (!item || !current || current instanceof Problem) throw new Problem(404, 'This character is not in the published collection.');
  // A glyph a review named is filed under the named character's family, and leaves its visual group.
  const named = current.data.identity_basis === 'human_review' ? current.label : null;
  const head = named ? await env.ATLAS.prepare('SELECT h.text FROM graphemes g LEFT JOIN graphemes h ON h.id=g.head WHERE g.text=?')
    .bind(named).first<{ text: string | null }>() : null;
  const formed = { ...item, form: await formOfCrop(env, crop!), issue: current.data.issue, written_character: current.data.written_character,
    ...(current.data.identity_status ? { identity_status: current.data.identity_status } : {}),
    ...(named ? { grapheme: codePoints(head?.text ?? named), visual_group: null } : {}) };
  return { ...formed, origin: 'corpus', event: { id: saved.id, revision: saved.revision, state: saved.state } };
}

// --- Undoing ---

export type Submission = { id: number; key: string; actor: number; at: number; undone: number };
/** Undo the user's own submission named by the id its client gave it, under any actor they hold. */
export async function undo(env: Env, request: Request, user: string, id: string) {
  await body(request);
  const found = await env.ATLAS.prepare(`SELECT id,key,actor,at,undone,request FROM submissions
    WHERE key IN (SELECT key||':'||?1 FROM actors WHERE user=?2)`).bind(id, user).first<Submission & { request: string }>();
  if (!found) throw new Problem(404, 'No saved round belongs to this reviewer.');
  // An admin's corpus flags are saved as a submission too; they are no review.
  if (JSON.parse(found.request).target === '@flags') throw new Problem(409, 'A flag is not a review, so it cannot be undone.');
  const { request: _, ...submission } = found;
  return { id, ...await revert(env, submission, await actorFor(env, user), undefined) };
}
type Reviewed = { id: number; crop: number; revision: number; cut: number; label: number | null; shown: number | null; shown_text: string | null;
  was_state: number | null; was_issue: number | null; was_x: number | null; was_y: number | null; was_w: number | null; was_h: number | null;
  x: number | null; reversed: number };
// What a publication event set (events.published; publish.py SET_BITS); one without the column set all.
const SET = { char: 1, state: 2, quiz: 4, cut: 8, kind: 16, flags: 32, all: 63 } as const;
/** Undo a submission as `actor`: each crop its reviews changed goes back to what it was before, unless
 *  a later review has changed it since; the crops its round showed or skipped take their marks without
 *  it once every review is undone. A rejection is the same undo made by an admin, recorded with its
 *  reason; passing `reviewer` (every actor of the reviewer) also passes over that reviewer's own later
 *  work already undone.
 *
 *  An undo never writes a column a later publication set (its event's `published`), and leaves it as the
 *  publication put it. A reviewer's own undo is refused, crop by crop, where a later publication set what
 *  the review set (its character, its state, the cut its box was drawn on), or where the crop moved on
 *  with no event, as older publications did; those crops are reported and stay undoable by an admin. The
 *  submission counts as undone only once every review in it is, and the reviewer's totals lose only the
 *  reviews undone. */
export async function revert(env: Env, submission: Submission, actor: number, rejection?: { by: string; reason: string; reviewer?: number[] }, again = true): Promise<{ results: Json[]; duplicate?: true }> {
  if (submission.undone) return { results: [], duplicate: true };
  const reviewed = (await env.ATLAS.prepare(`SELECT e.id,e.crop,e.revision,e.cut,e.label,e.shown,g.text AS shown_text,e.was_state,e.was_issue,e.was_x,e.was_y,e.was_w,e.was_h,
    e.x,EXISTS(SELECT 1 FROM events u WHERE u.reverses=e.id) AS reversed
    FROM events e LEFT JOIN graphemes g ON g.id=e.shown WHERE e.submission=? AND e.kind IN (0,1,2) ORDER BY e.id`).bind(submission.id).all<Reviewed>()).results;
  const open = reviewed.filter(r => !r.reversed);
  const read = await reviewCrops(env, open.map(r => r.crop));
  // Every event since each review, in one read.
  const after = new Map<number, { kind: number; published: number | null }[]>();
  if (open.length) for (const e of (await env.ATLAS.prepare(`SELECT j.value->>'id' AS review,e.kind,e.published FROM json_each(?) j
    JOIN crops c ON c.id=j.value->>'crop' JOIN events e ON e.crop=c.id AND e.revision>j.value->>'revision' AND e.revision<=c.revision`)
    .bind(JSON.stringify(open.map(r => ({ id: r.id, crop: r.crop, revision: r.revision })))).all<{ review: number; kind: number; published: number | null }>()).results)
    after.set(e.review, [...after.get(e.review) ?? [], e]);
  const at = Date.now(), changes: CropChange[] = [], results: Json[] = [];
  for (const r of open) {
    const current = read.get(r.crop)!;
    if (current instanceof Problem) throw current;
    const since = after.get(r.id) ?? [];
    const set = since.filter(e => e.kind === PUBLISHED).reduce((bits, e) => bits | (e.published ?? SET.all), 0);
    // What the review set: its state always, its character when it named another, its box when drawn.
    const wrote = SET.state | (r.label !== r.shown ? SET.char : 0) | (r.x !== null ? SET.cut : 0);
    const unseen = current.revision - r.revision > since.length;
    if (!rejection && ((set & wrote) || unseen)) {
      results.push({ crop: r.crop, refused: 'This crop was published again since this review.' });
      continue;
    }
    // Rejecting all of one reviewer's work also passes over their own later changes to the crop that
    // are already undone, and the undos themselves; anyone else's later change, or one of theirs that
    // still stands, stops it. Marks never do; nor does a publication, which is no one's review.
    const later = current.revision !== r.revision && (rejection?.reviewer ? await env.ATLAS.prepare(`SELECT 1 FROM events e JOIN submissions s ON s.id=e.submission
      WHERE e.crop=? AND e.revision>? AND e.kind NOT IN (3,4,${PUBLISHED}) AND (s.actor NOT IN (SELECT value FROM json_each(?)) OR (s.undone=0 AND e.kind IN (0,1,2))) LIMIT 1`)
      .bind(r.crop, r.revision, JSON.stringify(rejection.reviewer)).first()
      : since.some(e => e.kind !== PUBLISHED));
    if (later) {
      if (await env.ATLAS.prepare('SELECT 1 FROM submissions WHERE id=? AND undone=1').bind(submission.id).first())
        return { results: [], duplicate: true };
      throw new Problem(409, 'A later review changed this crop. It cannot be undone.');
    }
    const glyph = current.kind === 1, label = set & SET.char ? current.label : r.shown_text;
    const box = r.was_x != null && r.cut === current.cut && !(set & SET.cut) ? { x: r.was_x, y: r.was_y!, w: r.was_w!, h: r.was_h! } : null;
    // Queue eligibility from the record the undo restores, with the repair verdict it holds now.
    const restored = { ...current.data, written_character: glyph ? label : current.data.written_character, box: box ?? current.cutBox };
    const state = set & SET.state ? current.state : r.was_state ?? STATE.pending;
    changes.push({ crop: r.crop, expected: current.revision, cut: current.cut, kind: KIND.undo, label, state, mark: markable(state),
      issue: set & SET.state ? null : r.was_issue, named: null, shown: set & SET.char ? current.char : r.shown,
      quiz: set & SET.quiz ? null : dealable(glyph ? 'corpus' : 'local', restored) ? 1 : 0,
      ...(label !== current.label && label ? { category: categoryOf(label) } : {}), box, reverses: r.id });
    results.push({ crop: r.crop, revision: current.revision + 1, reverses: r.id });
  }
  const refused = results.filter(r => r.refused).length, complete = refused === 0;
  // Nothing to undo now, with crops refused: the submission stays open for an admin.
  if (!changes.length && !complete) return { results: results.map(r => ({ id: null, target_id: publicId(r.crop), refused: r.refused })) };
  const shownOrSkipped = complete ? (await env.ATLAS.prepare(`SELECT crop FROM seen WHERE submission=?1
    UNION SELECT crop FROM skips WHERE submission=?1`).bind(submission.id).all<{ crop: number }>()).results
    .map(row => row.crop).filter(crop => !reviewed.some(r => r.crop === crop)) : [];
  // Undone in full, the NOT NULL guard claims it atomically, and a second claimant rolls back its batch; in
  // part, each crop's own revision guard does.
  const statements = [...(complete ? [env.ATLAS.prepare('UPDATE submissions SET undone=CASE WHEN undone=0 THEN 1 END WHERE id=?').bind(submission.id)] : []),
    ...eventStatements(env, actor, submission.key, at, changes),
    ...(shownOrSkipped.length ? [markStatement(env, actor, submission.key, at, shownOrSkipped, true)] : []),
    ...applyStatements(env, submission.key, at, [KIND.undo], changes),
    env.ATLAS.prepare('UPDATE actor_stats SET reviews=coalesce(reviews,0)-?,rounds=coalesce(rounds,0)-? WHERE actor=?')
      .bind(changes.length, complete ? 1 : 0, submission.actor),
    ...(rejection ? [env.ATLAS.prepare('INSERT INTO rejections(submission,by,reason,at) VALUES(?,?,?,?)').bind(submission.id, rejection.by, rejection.reason, at)] : []),
    env.ATLAS.prepare('SELECT id,crop FROM events WHERE submission=? AND at=? AND kind=? AND reverses IS NOT NULL').bind(submission.id, at, KIND.undo)];
  let ids: Map<number, number>;
  try {
    const out = await env.ATLAS.batch<{ id: number; crop: number }>(statements);
    ids = new Map(out[out.length - 1].results.map(row => [row.crop, row.id]));
  } catch (error) {
    if (await env.ATLAS.prepare('SELECT 1 FROM submissions WHERE id=? AND undone=1').bind(submission.id).first())
      return { results: [], duplicate: true };
    if (conflict(error) && again && await marksOnly(env, changes)) return await revert(env, submission, actor, rejection, false);
    if (conflict(error)) throw new Problem(409, 'A later review changed this crop.');
    throw error;
  }
  return { results: results.map(r => r.refused ? { id: null, target_id: publicId(r.crop), refused: r.refused }
    : { id: ids.get(r.crop) ?? null, target_id: publicId(r.crop), revision: r.revision,
      review: { id: ids.get(r.crop) ?? null, kind: 'undo', reverses: r.reverses, at: new Date(at).toISOString() } }) };
}

// --- Routes ---

const json = (value: unknown, status = 200) => Response.json(value, { status, headers: { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' } });
/** The review writes, for a signed-in user; null for a path that is none of them. */
export async function reviewRoute(env: Env, request: Request, path: string, user: string): Promise<Response | null> {
  try {
    if (path === '/atlas/corpus/reviews') return json(await submit(env, request, user, '@corpus'));
    if (path === '/atlas/rounds') return json(await submit(env, request, user));
    if (path === '/atlas/corrections') {
      // A batch changes many crops at once, so each address is held to a rate.
      const { success } = await env.CORRECTIONS.limit({ key: addressKey(request.headers.get('cf-connecting-ip')) });
      if (!success) throw new Problem(429, 'Too many corrections at once. Wait a minute and try again.');
      return json(await submit(env, request, user, '@batch'));
    }
    const undone = path.match(/^\/atlas\/(?:rounds|corrections)\/([^/]+)\/undo$/);
    if (undone) return json(await undo(env, request, user, decodeURIComponent(undone[1])));
    const edit = path.match(/^\/(?:atlas\/characters|layers\/units)\/([^/]+)$/);
    if (edit) return json(await submit(env, request, user, decodeURIComponent(edit[1])));
    return null;
  } catch (error) {
    if (error instanceof StaleCrop) throw new Problem(409, error.message, { code: STALE_CODE });
    throw error;
  }
}
