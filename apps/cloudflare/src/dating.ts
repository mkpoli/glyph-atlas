// When the book a crop comes from was written, copied or printed (`book_dates` in glyph-atlas-v3).
// A listing gives each crop its book's `dating`: the date of the copy itself (`witness`) and of the
// text it carries (`composed`), as the publication resolved them from the ledger's `date_*` claims.
type Json = Record<string, any>;

const parsed = (value: string | null) => { if (!value) return null; try { return JSON.parse(value) } catch { return null } };

/** One axis of a book's dates as a listing carries it: the date, its words, and the HuTime query that converted it. */
export function axisOf(row: Json): Json {
  return { kind: row.kind, start: row.start, end: row.end, precision: row.precision, qualifier: row.qualifier ?? null,
    uncertain: Boolean(row.uncertain), text: row.text, label: row.label, status: row.status, calendar: row.calendar,
    source: row.source, hutime: parsed(row.conversion)?.query ?? null };
}

// A book's axes in glyph-atlas-v3, by its integer id: one primary-key range a book.
export const bookDatingQuery = () =>
  `SELECT book,axis,kind,start,end,precision,qualifier,uncertain,text,label,status,calendar,conversion,source
  FROM book_dates WHERE book IN (SELECT value FROM json_each(?))`;

/** Each named book's axes, from `ATLAS.book_dates`. */
export async function bookDating(env: Env, books: (number | null | undefined)[]): Promise<Map<number, Json>> {
  const wanted = [...new Set(books.filter((b): b is number => typeof b === 'number'))];
  const found = new Map<number, Json>();
  if (!wanted.length) return found;
  const rows = await env.ATLAS.prepare(bookDatingQuery()).bind(JSON.stringify(wanted)).all<Json>();
  for (const row of rows.results) found.set(row.book, { ...found.get(row.book), [row.axis]: axisOf(row) });
  return found;
}

// A gallery placed or narrowed by date: `order=year` lists its crops oldest first, undated last;
// `years=1600-1699` keeps those whose book's date begins in the range, as the decades count them and a
// time axis places them, and `years=undated` those whose has none. `axis` picks the date: the copy's (`witness`, the default) or its text's (`composed`).
export type YearOptions = { axis: 'witness' | 'composed'; order: 'style' | 'year'; years: { from: number; to: number } | 'undated' | null };
const YEARS = /^(-?\d{1,4})-(-?\d{1,4})$/;
export function yearOptions(q: URLSearchParams, fail: (status: number, message: string) => never): YearOptions | null {
  const order = q.get('order') === 'year' ? 'year' : 'style', raw = q.get('years');
  const axis = q.get('axis') === 'composed' ? 'composed' : 'witness';
  let years: YearOptions['years'] = null;
  if (raw === 'undated') years = 'undated';
  else if (raw) {
    const found = YEARS.exec(raw);
    if (!found || Number(found[1]) > Number(found[2])) fail(422, 'A year range is two years, the earlier first: 1600-1699.');
    years = { from: Number(found![1]), to: Number(found![2]) };
  }
  return order === 'year' || years || axis === 'composed' ? { axis, order, years } : null;
}
// The collection's dates in numbers (`/atlas/dates/stats`, routes/browse.ts): how many crops and works
// are dated, by hundred years and by decade of the year a work's copy is dated from, and by what the
// date dates. A work is a book.
type Tally = { crops: number; works: number };
const add = (map: Map<string | number, Tally>, key: string | number, crops: number, work: boolean) => {
  const found = map.get(key) ?? { crops: 0, works: 0 };
  found.crops += crops; if (work) found.works += 1;
  map.set(key, found);
};
/** The tallies of `rows`, one row a document's crops in one list (the collection's or the corpus's). */
export function tallyDates(rows: { document: string; n: number; kind: string | null; start: number | null; end: number | null; composed: number | null }[]) {
  const seen = new Set<string>(), hundreds = new Map<string | number, Tally>(), decades = new Map<string | number, Tally>(), kinds = new Map<string | number, Tally>();
  const total = { crops: 0, works: 0 }, dated = { crops: 0, works: 0 }, composed = { crops: 0, works: 0 };
  for (const row of rows) {
    const work = !seen.has(row.document); seen.add(row.document);
    total.crops += row.n; if (work) total.works += 1;
    // A date given only as before a year counts in that year, as the time axis places it.
    const year = row.start ?? row.end;
    if (row.kind) add(kinds, row.kind, row.n, work);
    if (row.composed !== null) { composed.crops += row.n; if (work) composed.works += 1 }
    if (year === null || year === undefined) continue;
    dated.crops += row.n; if (work) dated.works += 1;
    add(hundreds, Math.floor(year / 100) * 100, row.n, work);
    add(decades, Math.floor(year / 10) * 10, row.n, work);
  }
  const listed = (map: Map<string | number, Tally>) => [...map].map(([key, t]) => [key, t.crops, t.works]).sort((a, b) => (a[0] as number) - (b[0] as number));
  return { total, dated, composed, hundreds: listed(hundreds), decades: listed(decades),
    kinds: [...kinds].map(([kind, t]) => [kind, t.crops, t.works]) };
}
