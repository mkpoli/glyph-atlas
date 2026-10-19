type Json = any;

// A published record is a few kilobytes; one larger than this is not a record.
export const RECORD_MAX = 128 * 1024;
// Many corpus glyphs' published records at once: the records of one pack that lie near each other are read
// in one range, RECORD_STREAMS ranges at a time (a Worker holds six connections open and queues the rest). A page of a run's occurrences sits in a few packs, glyph after
// glyph, so it reads a handful of ranges rather than a record and a query per glyph. A record is a few
// kilobytes, so reading through a gap of up to RECORD_GAP (a dozen or so other glyphs) costs less than another
// range; RECORD_SPAN bounds what one range holds in memory. A record missing from its pack, one that does
// not parse, or one over RECORD_MAX is left out. A storage error fails the call: the
// answer is kept at the edge, and one quietly short of occurrences, or showing labels a decision has
// overturned, would be served until the cache let it go.
const RECORD_SPAN = 1024 * 1024, RECORD_GAP = 64 * 1024, RECORD_STREAMS = 6;
export type RecordPointer = { object: string; offset: number; size: number };
export function recordRanges(pointers: RecordPointer[]): { object: string; offset: number; end: number; members: RecordPointer[] }[] {
  const ranges: { object: string; offset: number; end: number; members: RecordPointer[] }[] = [];
  const sorted = [...pointers].filter(p => p.size <= RECORD_MAX)
    .sort((a, b) => a.object < b.object ? -1 : a.object > b.object ? 1 : a.offset - b.offset);
  for (const p of sorted) {
    const last = ranges[ranges.length - 1];
    if (last && last.object === p.object && p.offset - last.end <= RECORD_GAP && p.offset + p.size - last.offset <= RECORD_SPAN) {
      last.end = Math.max(last.end, p.offset + p.size); last.members.push(p);
    } else ranges.push({ object: p.object, offset: p.offset, end: p.offset + p.size, members: [p] });
  }
  return ranges;
}
export const recordKey = (p: RecordPointer) => `${p.object}:${p.offset}:${p.size}`;
// The records behind many pointers, by recordKey, read RECORD_STREAMS ranges at a time.
export async function readRecords(env: Env, pointers: RecordPointer[]): Promise<Map<string, Json>> {
  const raw = new Map<string, Json>(), decoder = new TextDecoder();
  const ranges = recordRanges(pointers);
  for (let start = 0; start < ranges.length; start += RECORD_STREAMS)
    await Promise.all(ranges.slice(start, start + RECORD_STREAMS).map(async range => {
      const object = await env.MEDIA.get(range.object, { range: { offset: range.offset, length: range.end - range.offset } });
      if (!object) return;
      const bytes = new Uint8Array(await object.arrayBuffer());
      for (const p of range.members) {
        try { raw.set(recordKey(p), JSON.parse(decoder.decode(bytes.subarray(p.offset - range.offset, p.offset - range.offset + p.size)))) } catch { /* left out */ }
      }
    }));
  return raw;
}
