// The 振り仮名 a transcription gives a line: the R2 packs scripts/atlas3/rubies.py writes under
// `v3/rubies/<entry>/<n>.json.gz`, 32 pages of one みんなで翻刻 entry each, read by key with no D1 read.
// A line is found by its key, its letters and numbers alone, so the punctuation a dataset changed does
// not part a published line from its transcription.
import { heldObjects } from './atlas3Media';

// [start, length, side (0 right, 1 left), reading: one string over the base, or one for each character]
export type LineRuby = [number, number, 0 | 1, string | string[]];
type Pack = Record<string, Record<string, LineRuby[]>>;
const PREFIX = 'v3/rubies/', PACK_PAGES = 32;
const readPack = heldObjects<Pack>(8);

/** A line's key: its characters that are letters or numbers. */
export const lineKey = (text: string) => [...text.normalize('NFC')].filter(char => /[\p{L}\p{N}]/u.test(char)).join('');
/** Where a page's rubies are kept, or null for a page that is no みんなで翻刻 entry's. */
export function rubyPack(page: unknown, number: unknown): string | null {
  const entry = typeof page === 'string' ? /^[a-z]+:([0-9a-f]{32}):\d+$/i.exec(page)?.[1] : null;
  return entry && Number.isSafeInteger(number) && (number as number) > 0 ? `${PREFIX}${entry.toLowerCase()}/${Math.floor(((number as number) - 1) / PACK_PAGES)}.json.gz` : null;
}
// [start, length over the line's key, reading as a ruby gives it, the lines that give it, a page of one]
export type RubyElsewhere = [number, number, string | string[], number, number];
type Given = { reading: string | string[]; lines: number; page: number };
const BASE_MAX = 12, ELSEWHERE_MAX = 24, READINGS_EACH = 2;
const packWords = new WeakMap<Pack, Map<string, Map<string, Given>>>();
/** The words of a pack: each base a right ruby stands over, with its readings, the lines that give each and a page of one. */
function wordsOf(pack: Pack): Map<string, Map<string, Given>> {
  let words = packWords.get(pack);
  if (words) return words;
  words = new Map();
  for (const [page, lines] of Object.entries(pack)) for (const [key, rubies] of Object.entries(lines)) {
    const chars = [...key], seen = new Set<string>();
    for (const [start, length, side, reading] of rubies) {
      if (side !== 0 || length > BASE_MAX) continue;
      const base = chars.slice(start, start + length).join(''), text = [reading].flat().join('');
      if (!base || !text || seen.has(`${base}\n${text}`)) continue;
      seen.add(`${base}\n${text}`);
      let readings = words.get(base);
      if (!readings) words.set(base, readings = new Map());
      const given = readings.get(text);
      // A reading cut over its characters says more than the same one over the whole base.
      if (given) { given.lines++; if (Array.isArray(reading) && !Array.isArray(given.reading)) given.reading = reading; }
      else readings.set(text, { reading, lines: 1, page: Number(page) });
    }
  }
  packWords.set(pack, words);
  return words;
}
/**
 * What the transcription gives a crop's line: `here`, the rubies over the characters of its key, and `elsewhere`,
 * the readings the same 32 pages give a word of the line that stands here under no ruby, longest word first.
 */
export async function lineRubies(env: Env, record: { page_id?: unknown; page_number?: unknown; text?: unknown }, ctx?: ExecutionContext): Promise<{ here: LineRuby[] | null; elsewhere: RubyElsewhere[] }> {
  const name = rubyPack(record.page_id, record.page_number);
  if (!name || typeof record.text !== 'string' || !record.text) return { here: null, elsewhere: [] };
  const pack = await readPack(env, name, ctx);
  if (!pack) return { here: null, elsewhere: [] };
  const lines = pack[String(record.page_number)];
  const key = lineKey(record.text), here = lines && Object.hasOwn(lines, key) ? lines[key] : null;
  const words = wordsOf(pack), chars = [...key], elsewhere: RubyElsewhere[] = [];
  const under = (start: number, length: number) => (here ?? []).some(([from, size, side]) => side === 0 && start < from + size && from < start + length);
  for (let length = Math.min(BASE_MAX, chars.length); length > 0 && elsewhere.length < ELSEWHERE_MAX; length--) for (let start = 0; start + length <= chars.length; start++) {
    const readings = words.get(chars.slice(start, start + length).join(''));
    if (!readings || under(start, length)) continue;
    for (const given of [...readings.values()].sort((a, b) => b.lines - a.lines).slice(0, READINGS_EACH)) elsewhere.push([start, length, given.reading, given.lines, given.page]);
  }
  return { here, elsewhere: elsewhere.slice(0, ELSEWHERE_MAX) };
}
