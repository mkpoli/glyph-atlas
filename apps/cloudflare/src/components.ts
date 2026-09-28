// Characters found by their components: 水骨, 氵骨 and 氵冖月 each find 滑. The tables are
// `han_components` and `han_component_names` (migration 0033), built by src/glyph_atlas/han_components.py,
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

// The rarest component's characters in key order and at most COMPONENT_SCAN of them, each looked up
// by key in the list of every other component. Returns code_point, tier, size and how many of the asked
// components the character names at its top level.
export function componentMatchQuery(others: number): string {
  const joins = Array.from({ length: others }, (_, i) =>
    ` JOIN han_components o${i} ON o${i}.component=? AND o${i}.tier=x.tier AND o${i}.size=x.size AND o${i}.code_point=x.code_point AND o${i}.n>=?`).join('');
  const direct = ['x.direct', ...Array.from({ length: others }, (_, i) => `o${i}.direct`)].join('+');
  return `SELECT x.code_point,x.tier,x.size,${direct} AS direct FROM (SELECT code_point,tier,size,n,direct FROM han_components
    WHERE component=? ORDER BY tier,size,code_point LIMIT ${COMPONENT_SCAN}) x${joins} WHERE x.n>=?`;
}

type Match = { code_point: string; tier: number; size: number; direct: number };

// More asked components at the top level first (明 before 胄 for 日月), then everyday characters, then simpler ones.
export function rankMatches(rows: Match[]): Match[] {
  return [...rows].sort((a, b) => b.direct - a.direct || a.tier - b.tier || a.size - b.size
    || parseInt(a.code_point.slice(2), 16) - parseInt(b.code_point.slice(2), 16));
}

export async function componentSearch(env: Env, wanted: Map<string, number>, limit: number) {
  const typed = [...wanted.keys()];
  const names = await env.DB.prepare(`SELECT query,component,total FROM han_component_names WHERE query IN (${typed.map(() => '?').join(',')})`)
    .bind(...typed).all<{ query: string; component: string; total: number }>();
  if (names.results.length < typed.length) return { codes: [], more: false };
  // Two typed forms of one component (⺡ and 氵) ask for it twice.
  const parts = new Map<string, { n: number; total: number }>();
  for (const row of names.results) {
    const part = parts.get(row.component);
    parts.set(row.component, { n: (part?.n ?? 0) + wanted.get(row.query)!, total: row.total });
  }
  const [driver, ...others] = [...parts].sort((a, b) => a[1].total - b[1].total);
  const found = await env.DB.prepare(componentMatchQuery(others.length))
    .bind(driver[0], ...others.flatMap(([component, { n }]) => [component, n]), driver[1].n).all<Match>();
  const ranked = rankMatches(found.results);
  return { codes: ranked.slice(0, limit).map(row => row.code_point),
    more: ranked.length > limit || driver[1].total > COMPONENT_SCAN };
}
