// What the admin page reads: who has saved work, and each reviewer's submissions.
type Json = Record<string, any>;
const PAGE = 50;
// The crops a submission lists by name, to open from the admin page; a larger one says how many more.
const SHOWN = 24;

// A user's reviewer ids, as a subquery on the `u` row.
const HELD = 'SELECT actor FROM actors WHERE user_id=u.id';

/**
 * Reviewers, the most recently active first: users that saved something (or every account, for
 * `accounts`), and the ids from before accounts that nobody has claimed (`unclaimed`).
 */
export async function reviewers(env: Env, q: URLSearchParams) {
  const kind = q.get('kind') ?? 'active', offset = Math.max(0, Number(q.get('offset') ?? 0) | 0);
  const find = (q.get('q') ?? '').trim().slice(0, 64);
  if (kind === 'unclaimed') {
    const rows = await env.DB.prepare(`SELECT actor,count(*) AS submissions,max(at) AS last FROM submissions
      WHERE actor NOT IN (SELECT actor FROM actors) AND (?='' OR actor LIKE '%'||?||'%') GROUP BY actor ORDER BY last DESC LIMIT ? OFFSET ?`)
      .bind(find, find, PAGE + 1, offset).all<Json>();
    return page(rows.results.map(r => ({ actor: r.actor, name: r.actor, submissions: r.submissions, last: r.last })), offset);
  }
  const where = [`(?='' OR u.name LIKE '%'||?||'%' OR u.email LIKE '%'||?||'%' OR u.id=?)`];
  if (kind === 'accounts') where.push('coalesce(u.isAnonymous,0)=0');
  if (kind === 'anonymous') where.push('u.isAnonymous=1');
  if (kind === 'banned') where.push('u.banned=1');
  if (kind === 'admins') where.push(`u.role='admin'`);
  const rows = await env.DB.prepare(`SELECT * FROM (SELECT u.id,u.name,u.email,u.image,u.role,u.banned,u.banReason,u.isAnonymous,u.createdAt,u.lastLoginMethod,
      (SELECT count(*) FROM submissions WHERE actor IN (${HELD})) AS submissions,
      (SELECT max(at) FROM submissions WHERE actor IN (${HELD})) AS last,
      (SELECT count(*) FROM rejections WHERE submission IN (SELECT id FROM submissions WHERE actor IN (${HELD}))) AS rejected
    FROM "user" u WHERE ${where.join(' AND ')}) ${kind === 'active' ? 'WHERE last IS NOT NULL' : ''}
    ORDER BY last IS NULL,last DESC,createdAt DESC LIMIT ? OFFSET ?`).bind(find, find, find, find, PAGE + 1, offset).all<Json>();
  return page(rows.results.map(r => ({ ...r, banned: Boolean(r.banned), anonymous: Boolean(r.isAnonymous), isAnonymous: undefined,
    // An anonymous user's address is a placeholder, not one anybody reads.
    email: r.isAnonymous ? null : r.email })), offset);
}

const page = (rows: Json[], offset: number) => ({ items: rows.slice(0, PAGE), next: rows.length > PAGE ? offset + PAGE : null });

/** One reviewer's submissions, newest first, each with what it decided and whether it still stands. */
export async function submissions(env: Env, q: URLSearchParams, actors: [string, string[]]) {
  const offset = Math.max(0, Number(q.get('offset') ?? 0) | 0);
  const rows = await env.DB.prepare(`SELECT s.id,s.actor,s.at,s.undone,s.request,r.by,r.reason,r.at AS rejected_at,ru.name AS rejected_by,
      (SELECT count(*) FROM events e WHERE e.submission=s.id AND e.kind='review') AS crops,
      (SELECT json_group_array(json_object('id',target,'label',label)) FROM (SELECT e.target,json_extract(e.before_data,'$.label') AS label
        FROM events e WHERE e.submission=s.id AND e.kind='review' ORDER BY e.rowid LIMIT ${SHOWN})) AS targets
    FROM submissions s LEFT JOIN rejections r ON r.submission=s.id LEFT JOIN "user" ru ON ru.id=r.by
    WHERE s.actor IN ${actors[0]} ORDER BY s.at DESC,s.id DESC LIMIT ? OFFSET ?`).bind(...actors[1], PAGE + 1, offset).all<Json>();
  return page(rows.results.map(row => ({ id: row.id, at: row.at, crops: row.crops, ...summary(row.request),
    targets: JSON.parse(row.targets ?? '[]') as { id: string; label: string | null }[],
    state: row.by ? 'rejected' : row.undone ? 'undone' : 'standing',
    rejection: row.by ? { by: row.rejected_by ?? row.by, reason: row.reason, at: row.rejected_at } : null })), offset);
}

/** What a submission asked for, from the request it was saved with. */
export function summary(request: string): Json {
  let signed: Json;
  try { signed = JSON.parse(request) } catch { return { kind: 'other' } }
  const input = signed.input ?? {}, target = signed.target;
  const verdicts = (answers: Json[] = []) => answers.reduce((counts: Json, answer) => {
    const key = answer?.verdict ?? 'other'; counts[key] = (counts[key] ?? 0) + 1; return counts }, {});
  if (target === '@batch') return { kind: 'correction', label: input.character ?? null, verdicts: { wrong: input.crops?.length ?? 0 } };
  if (target === null) return { kind: 'round', label: input.label ?? null, verdicts: verdicts(input.answers),
    skipped: input.skipped?.length ?? 0 };
  return { kind: 'crop', target, label: input.character ?? input.reading ?? null, verdicts: verdicts([input]) };
}
