// When the book a crop comes from was written, copied or printed (migration 0052, `glyph_atlas.dates`).
// A listing gives each crop its document's `dating`: the date of the copy itself (`witness`) and of the
// text it carries (`composed`), as the publication resolved them from the ledger's `date_*` claims. The
// inspector also lists the claims themselves, each with its words, source and locator.
type Json = Record<string, any>;

const AXIS_COLUMNS = 'document,axis,kind,start,end,precision,qualifier,uncertain,text,label,status,calendar,conversion,source';
// D1 binds at most 100 values a statement.
const CHUNK = 90;

/** The document a listing item comes from: a crop's row, or a corpus record's source. */
export const documentOf = (item: Json): string | null => item.document ?? item.source?.document_id ?? null;
export const datingQuery = (n: number) =>
  `SELECT ${AXIS_COLUMNS} FROM document_dating WHERE document IN (${Array(n).fill('?').join(',')})`;
// A document's date claims that stand: every `date_*` assertion its source has not retracted.
export const dateClaimsQuery = () => `SELECT a.id,a.predicate,a.value,a.tier,e.ref AS source,e.locator
  FROM assertions a LEFT JOIN assertion_evidence e ON e.assertion=a.id AND e.kind='source'
  WHERE a.subject=? AND substr(a.predicate,1,5)='date_'
    AND NOT EXISTS (SELECT 1 FROM assertion_actions x WHERE x.assertion=a.id AND x.action='retract')
  ORDER BY a.predicate,a.id`;

const parsed = (value: string | null) => { if (!value) return null; try { return JSON.parse(value) } catch { return null } };

/** One axis of `document_dating` as a listing carries it: the date, its words, and the HuTime query that converted it. */
export function axisOf(row: Json): Json {
  return { kind: row.kind, start: row.start, end: row.end, precision: row.precision, qualifier: row.qualifier ?? null,
    uncertain: Boolean(row.uncertain), text: row.text, label: row.label, status: row.status, calendar: row.calendar,
    source: row.source, hutime: parsed(row.conversion)?.query ?? null };
}

/** Each named document's axes, read in as few statements as D1 allows. */
export async function datingOf(env: Env, documents: (string | null | undefined)[]): Promise<Map<string, Json>> {
  const wanted = [...new Set(documents.filter((d): d is string => Boolean(d)))];
  const found = new Map<string, Json>();
  if (!wanted.length) return found;
  const statements = [];
  for (let i = 0; i < wanted.length; i += CHUNK) {
    const part = wanted.slice(i, i + CHUNK);
    statements.push(env.DB.prepare(datingQuery(part.length)).bind(...part));
  }
  for (const result of await env.DB.batch(statements))
    for (const row of result.results as Json[]) found.set(row.document, { ...found.get(row.document), [row.axis]: axisOf(row) });
  return found;
}

/** `items`, each with its document's `dating`: its axes, or `{}` for a book no source dates. */
export async function withDating<T extends Json>(env: Env, items: T[], documentFor: (item: T) => string | null = documentOf): Promise<T[]> {
  const dating = await datingOf(env, items.map(documentFor));
  return items.map(item => ({ ...item, dating: dating.get(documentFor(item) ?? '') ?? {} }));
}

/** A document's dating with the claims it was resolved from, for the inspector. */
export async function documentDates(env: Env, document: string | null): Promise<Json> {
  if (!document) return { dating: {} };
  const [axes, claims] = await env.DB.batch([
    env.DB.prepare(datingQuery(1)).bind(document),
    env.DB.prepare(dateClaimsQuery()).bind(document),
  ]);
  const dating = Object.fromEntries((axes.results as Json[]).map(row => [row.axis, axisOf(row)]));
  const dates = (claims.results as Json[]).map(row => {
    const value = parsed(row.value) ?? {};
    return { id: row.id, kind: String(row.predicate).slice(5), of: value.of, text: value.text, start: value.start, end: value.end,
      precision: value.precision, qualifier: value.qualifier ?? null, uncertain: Boolean(value.uncertain), day: value.day ?? null,
      calendar: value.calendar, hutime: value.conversion?.query ?? null, note: value.note ?? null, tier: row.tier,
      source: row.source, locator: row.locator };
  });
  return { dating, ...(dates.length ? { dates } : {}) };
}

// A gallery placed or narrowed by date: `order=year` lists its crops oldest first, undated last;
// `years=1601-1700` keeps those whose book's date overlaps the range, `years=undated` those whose has
// none. `axis` picks the date: the copy's (`witness`, the default) or its text's (`composed`).
export type YearOptions = { axis: 'witness' | 'composed'; order: 'style' | 'year'; years: { from: number; to: number } | 'undated' | null };
const YEARS = /^(-?\d{1,4})-(-?\d{1,4})$/;
export function yearOptions(q: URLSearchParams, fail: (status: number, message: string) => never): YearOptions | null {
  const order = q.get('order') === 'year' ? 'year' : 'style', raw = q.get('years');
  const axis = q.get('axis') === 'composed' ? 'composed' : 'witness';
  let years: YearOptions['years'] = null;
  if (raw === 'undated') years = 'undated';
  else if (raw) {
    const found = YEARS.exec(raw);
    if (!found || Number(found[1]) > Number(found[2])) fail(422, 'A year range is two years, the earlier first: 1601-1700.');
    years = { from: Number(found![1]), to: Number(found![2]) };
  }
  return order === 'year' || years || axis === 'composed' ? { axis, order, years } : null;
}
/** The join that gives each crop of `column`'s book its date on the chosen axis, as `d`. */
export const datingJoin = (column: string, axis: YearOptions['axis']) =>
  ` LEFT JOIN document_dating d ON d.document=${column} AND d.axis='${axis === 'composed' ? 'composed' : 'witness'}'`;
// A dated crop's first year; for a date with only an end (`before 1600`), that end.
export const YEAR_KEY = 'coalesce(d.start,d.end)';
/** The condition `years` puts on the joined date, and its values. */
export function yearCondition(years: YearOptions['years']): { sql: string; values: number[] } {
  if (years === null) return { sql: '', values: [] };
  if (years === 'undated') return { sql: ` AND ${YEAR_KEY} IS NULL`, values: [] };
  return { sql: ` AND coalesce(d.end,d.start)>=? AND ${YEAR_KEY}<=?`, values: [years.from, years.to] };
}
/** Oldest first, undated last, then `rest`. */
export const yearOrder = (rest: string) => `${YEAR_KEY} IS NULL,${YEAR_KEY},coalesce(d.end,d.start),${rest}`;
// How many crops each decade holds, `decade` null for the undated.
export const decadeColumn = `CASE WHEN ${YEAR_KEY} IS NULL THEN NULL ELSE (${YEAR_KEY} - (((${YEAR_KEY} % 10) + 10) % 10)) END`;
