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
/** The rubies the transcription gives a crop's line, over the characters of its key; null for none. */
export async function lineRubies(env: Env, record: { page_id?: unknown; page_number?: unknown; text?: unknown }, ctx?: ExecutionContext): Promise<LineRuby[] | null> {
  const name = rubyPack(record.page_id, record.page_number);
  if (!name || typeof record.text !== 'string' || !record.text) return null;
  const pack = await readPack(env, name, ctx);
  const lines = pack?.[String(record.page_number)];
  const key = lineKey(record.text);
  return lines && Object.hasOwn(lines, key) ? lines[key] : null;
}
