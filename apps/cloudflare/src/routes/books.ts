// The books of the published collection: what a reader finds by a word of a title or a date, and one
// book's pages in reading order. The local review service serves the fuller record, with each book's
// shelfmark, holder and credit; this site holds the title, the dates and the publication's counts, and
// names a page by the key `/atlas/pages/:key` already serves. A page's photo is read when the page is
// opened, so a list of books never reads a bundle.
import { bookCounts } from '../atlas3Answers';
import { Problem } from '../busy';
import { integer, text } from '../input';
import { axisOf } from '../dating';

type Json = Record<string, any>;
type BookRow = { id: number; key: string; title: string | null };
type PageRow = { id: number; key: string };
type PageCountRow = { book: number; n: number; corpus: number };
type DateRow = { book: number; axis: 'witness' | 'composed' } & Json;

/** A book as the list and the page view show it. A field the publication does not hold is null. */
export function bookItem(row: BookRow, pageCount: number, boxCount: number, dating: Json): Json {
  return { id: row.key, title: row.title, shelfmark: null, genre: [], production: null,
    holder: null, attribution: null, licence: null, rights_url: null,
    dating, cover_url: null, page_count: pageCount, box_count: boxCount };
}

/** A page's number, from the key's last segment (`hk:book:12` is 12), or null when the key carries none. */
export function pageSeq(key: string): number | null {
  const tail = key.slice(key.lastIndexOf(':') + 1);
  return /^\d+$/.test(tail) ? Number(tail) : null;
}

// A script written without spaces: a word in it is found anywhere inside a field.
const UNSPACED = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\p{Script=Tangut}\u{3D000}-\u{3FC3F}]/u;
const LETTER = /[\p{L}\p{N}\p{M}]/u;

/** Whether `word` opens one of `text`'s words, with accented Latin letters counting as letters. */
function startsWord(text: string, word: string): boolean {
  for (let at = text.indexOf(word); at >= 0; at = text.indexOf(word, at + 1)) {
    if (at === 0) return true;
    const before = [...text.slice(0, at)].at(-1);
    if (before === undefined || !LETTER.test(before)) return true;
  }
  return false;
}

/** Whether a book answers to a word: a spaced-script word opens one of its words, and Han, kana,
 *  Hangul, Tangut and small seal are found anywhere inside, as the local review service matches them. */
export function bookSays(book: Json, word: string): boolean {
  const fields = [book.title, book.id,
    ...Object.values(book.dating).flatMap((date: any) => [date?.text, date?.label])];
  for (const field of fields) {
    const text = String(field ?? '').toLowerCase();
    if (!text) continue;
    if (UNSPACED.test(word) ? text.includes(word) : startsWord(text, word)) return true;
  }
  return false;
}

// A book never changes between publications, so an isolate builds the list once per publication: the
// root layout asks whether books exist on every page, and that probe must not read the catalogue again.
type Index = { pub: string; books: Json[] };
let held: { pub: string; read: Promise<Index> } | null = null;

async function build(env: Env, pub: string, counts: Map<number, [number, number]>): Promise<Index> {
  const [rows, pages, dates] = await env.ATLAS.batch([
    env.ATLAS.prepare('SELECT id,key,title FROM books'),
    env.ATLAS.prepare("SELECT book,count(*) AS n,sum(substr(key,1,7)='corpus:') AS corpus FROM pages GROUP BY book"),
    env.ATLAS.prepare(`SELECT book,axis,kind,start,end,precision,qualifier,uncertain,text,label,status,calendar,conversion,source
      FROM book_dates`),
  ]);
  const pageCounts = new Map((pages.results as PageCountRow[]).map(row => [row.book, row]));
  const dating = new Map<number, Json>();
  for (const row of dates.results as DateRow[]) dating.set(row.book, { ...dating.get(row.book), [row.axis]: axisOf(row) });
  // A corpus page's bundle carries no page record, so `/atlas/pages/:key` cannot open it. A book is
  // listed while one of its pages can open: the collected books, not the corpus-only ones.
  const books = (rows.results as BookRow[]).flatMap(row => {
    const pages = pageCounts.get(row.id);
    if (!pages || pages.corpus > 0) return [];
    const found = counts.get(row.id);
    return [bookItem(row, pages.n, found ? found[0] + found[1] : 0, dating.get(row.id) ?? {})];
  });
  books.sort((a, b) => {
    const x = String(a.title ?? a.id).toLowerCase(), y = String(b.title ?? b.id).toLowerCase();
    return x < y ? -1 : x > y ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
  return { pub, books };
}

async function index(env: Env): Promise<Index> {
  const { pub, books } = await bookCounts(env);
  if (held?.pub === pub) return held.read;
  const read = build(env, pub, books);
  held = { pub, read };
  read.catch(() => { if (held?.read === read) held = null; });
  return read;
}

function list(books: Json[], params: URLSearchParams): Json {
  const query = text(params.get('q'), 200, 'query') ?? '';
  const limit = integer(params, 'limit', 48, 100);
  if (limit < 1) throw new Problem(422, 'Invalid limit.');
  const offset = integer(params, 'offset', 0, 1_000_000);
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const found = words.length ? books.filter(book => words.every(word => bookSays(book, word))) : books;
  return { query: query || null, total: found.length, limit, offset, books: found.slice(offset, offset + limit) };
}

async function detail(env: Env, key: string): Promise<Json> {
  const book = await env.ATLAS.prepare('SELECT id,key,title FROM books WHERE key=?').bind(key).first<BookRow>();
  if (!book) throw new Problem(404, `No book ${key}.`);
  const [pages, counts, dates] = await env.ATLAS.batch([
    env.ATLAS.prepare("SELECT id,key FROM pages WHERE book=? AND substr(key,1,7)<>'corpus:'").bind(book.id),
    env.ATLAS.prepare('SELECT id/16777216 AS page,count(*) AS n FROM crops WHERE book=? AND kind<2 GROUP BY page').bind(book.id),
    env.ATLAS.prepare(`SELECT axis,kind,start,end,precision,qualifier,uncertain,text,label,status,calendar,conversion,source
      FROM book_dates WHERE book=?`).bind(book.id),
  ]);
  const units = new Map((counts.results as { page: number; n: number }[]).map(row => [row.page, row.n]));
  const dating: Json = {};
  for (const row of dates.results as DateRow[]) dating[row.axis] = axisOf(row);
  const ordered = (pages.results as PageRow[]).map(row => ({ row, seq: pageSeq(row.key) }))
    .sort((a, b) => (a.seq ?? Number.MAX_SAFE_INTEGER) - (b.seq ?? Number.MAX_SAFE_INTEGER) || a.row.id - b.row.id)
    .map(({ row, seq }, position) => ({ id: row.key, seq: seq ?? position, units: units.get(row.id) ?? 0,
      width: null, height: null, thumb_url: null }));
  const boxCount = ordered.reduce((sum, page) => sum + page.units, 0);
  return { ...bookItem(book, ordered.length, boxCount, dating), pages: ordered };
}

const json = (value: unknown, status = 200) => Response.json(value, { status,
  headers: { 'cache-control': 'public, max-age=60', 'x-content-type-options': 'nosniff' } });

/** `/atlas/books` and `/atlas/books/{key}`, or null for another path. */
export async function booksRoute(env: Env, url: URL): Promise<Response | null> {
  if (url.pathname === '/atlas/books' || url.pathname === '/atlas/books/') {
    const { books } = await index(env);
    return json(list(books, url.searchParams));
  }
  if (!url.pathname.startsWith('/atlas/books/')) return null;
  let key: string;
  try { key = decodeURIComponent(url.pathname.slice('/atlas/books/'.length)); } catch { throw new Problem(404, 'No such book.'); }
  if (!key || key.includes('/')) throw new Problem(404, 'No such book.');
  return json(await detail(env, key));
}
