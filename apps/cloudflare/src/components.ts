// Characters found by their components: 水骨, 氵骨 and 氵冖月 each find 滑. The component lists are R2
// objects (`atlas3Reference.componentMatches`), from the index src/glyph_atlas/han_components.py builds,
// which answers the same query for the local review app in the same order.

// How many characters the rarest component's list is read for. It is read everyday and simple
// characters first, so a search of very common components (一口) ranks those and says there are more.
export const COMPONENT_SCAN = 3000;
// A term of more characters than this is not a component search.
export const COMPONENT_PARTS = 8;

// Ideographs, radicals and strokes. NFKC reads a Kangxi Radicals character as its ideograph (⽔ is 水).
const PART = /^[\p{Script=Han}㇀-㇯]$/u;

export function componentTerm(term: string): Map<string, number> | null {
  const chars = [...term.normalize('NFKC').replace(/\s+/g, '')];
  if (chars.length < 2 || chars.length > COMPONENT_PARTS || !chars.every(c => PART.test(c))) return null;
  const wanted = new Map<string, number>();
  for (const c of chars) wanted.set(c, (wanted.get(c) ?? 0) + 1);
  return wanted;
}

export type Match = { code_point: string; tier: number; size: number; direct: number };

// More asked components at the top level first (明 before 胄 for 日月), then everyday characters, then simpler ones.
export function rankMatches(rows: Match[]): Match[] {
  return [...rows].sort((a, b) => b.direct - a.direct || a.tier - b.tier || a.size - b.size
    || parseInt(a.code_point.slice(2), 16) - parseInt(b.code_point.slice(2), 16));
}
