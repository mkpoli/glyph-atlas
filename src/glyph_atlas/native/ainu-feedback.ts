// Read-only adapter to ainu-records. Import its parser and validators at runtime.
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { tmpdir } from "node:os";

const digest = (value: string) => createHash("sha256").update(value, "utf8").digest("hex");
const object = (value: unknown): value is Record<string, any> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const fail = (message: string): never => { throw new Error(message); };
function fileText(path: string): string | null {
  try { return readFileSync(path, "utf8"); }
  catch (error: any) { if (error.code !== "ENOENT") throw error; return null; }
}

function correctionRecord(value: unknown): Record<string, any> {
  if (!object(value)) fail("A correction must be an object");
  const allowed = new Set(["id", "line", "original", "corrected", "note", "kind", "rubyField", "rubyBase", "entry"]);
  if (Object.keys(value).some(key => !allowed.has(key))) fail("Unexpected correction field");
  if (typeof value.id !== "string" || !/^[a-z][a-z0-9-]*$/.test(value.id)
    || !Number.isInteger(value.line) || value.line < 1) fail("Invalid correction identity or line");
  if (typeof value.original !== "string" || !value.original.trim()
    || typeof value.corrected !== "string" || value.original === value.corrected
    || typeof value.note !== "string" || !value.note.trim()) fail(`Invalid correction: ${value.id}`);
  if ([value.original, value.corrected].some(text => /[\r\n]/.test(text)))
    fail(`A correction cannot change line structure: ${value.id}`);
  if (value.entry !== undefined && (!object(value.entry)
    || Object.keys(value.entry).some(key => !["gloss", "form", "field"].includes(key))))
    fail(`Invalid entry target: ${value.id}`);
  return value;
}

async function prepare(root: string, input: any) {
  if (input?.version !== 1 || !Array.isArray(input.proposals)
    || input.proposals.length < 1 || input.proposals.length > 100) fail("Invalid source submission");
  const moduleAt = (name: string) => import(pathToFileURL(join(root, "scripts/lib", name)).href);
  const native = await moduleAt("corrections.ts");
  const { loadUnits } = await moduleAt("sources.ts");
  const units = await loadUnits();
  const existing = native.readCorrections();
  const seen = new Set(existing.map((c: any) => c.id));
  const resolved: any[] = [];
  const conflicts: any[] = [];
  const fileRecords = new Map<string, any[]>();
  const beforeFiles = new Map<string, string | null>();
  const touched = new Set<any>();
  for (const [index, item] of input.proposals.entries()) {
    try {
      if (!object(item)) fail("A proposal must be an object");
      const c = correctionRecord(item.correction);
      if (seen.has(c.id)) fail(`Correction id already exists: ${c.id}`);
      if (typeof item.entry !== "string" || !item.entry
        || !Number.isInteger(item.page_index) || item.page_index < 0
        || typeof item.canvas !== "string" || !item.canvas
        || typeof item.text_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.text_sha256))
        fail(`Missing source page identity or text checksum: ${c.id}`);
      const matches = units.filter((u: any) => u.platform === "honkoku" && u.part.entry === item.entry);
      if (matches.length !== 1) fail(`Entry does not identify one publishing unit: ${c.id}`);
      const unit = matches[0];
      const page = unit.pages.find((p: any) => p.index === item.page_index);
      const canvas = unit.entry.canvases[item.page_index];
      if (!page || !canvas || canvas.id !== item.canvas || page.canvasId !== item.canvas)
        fail(`Source page or canvas changed: ${c.id}`);
      if (digest(page.text) !== item.text_sha256) fail(`Source transcription changed: ${c.id}`);
      const unitKey = `${unit.source.slug}/${unit.slug}`;
      if (!/^[a-z0-9-]+\/[a-z0-9-]+$/.test(unitKey)) fail("Invalid publishing unit slug");
      const pageNumber = item.page_index + 1;
      const path = `data/editorial/corrections/${unitKey}/p${pageNumber}.json`;
      if (!beforeFiles.has(path)) beforeFiles.set(path, fileText(join(root, path)));
      seen.add(c.id);
      touched.add(unit);
      fileRecords.set(path, [...(fileRecords.get(path) ?? []), c]);
      resolved.push({ id: c.id, entry: item.entry, unit: unitKey, page: pageNumber,
        canvas: item.canvas, text_sha256: item.text_sha256, path });
    } catch (error) {
      conflicts.push({ index, id: item?.correction?.id ?? null, reason: String((error as Error).message) });
    }
  }
  if (conflicts.length) return { version: 1, validated: false, files: [], proposals: resolved, conflicts };
  // Use the native file loader for typed allograph/ruby/entry normalization too.
  const staging = mkdtempSync(join(tmpdir(), "atlas-source-validation-"));
  let normalized: any[];
  try {
    for (const [path, records] of fileRecords) {
      const relative = path.slice("data/editorial/corrections/".length);
      mkdirSync(join(staging, relative, ".."), { recursive: true });
      writeFileSync(join(staging, relative), JSON.stringify(records));
    }
    normalized = native.readCorrections(staging);
  } finally { rmSync(staging, { recursive: true, force: true }); }
  const decisions = [...native.readCorrections(), ...normalized];
  // This checks global ids, native text/ruby matching and overlap with existing decisions.
  native.validateCorrections(units, decisions);
  const { parseSourceWordlist } = await moduleAt("wordlist-layout.ts");
  for (const unit of touched) {
    const unitKey = `${unit.source.slug}/${unit.slug}`;
    let carry: any = null;
    for (let index = 0; index < unit.entry.canvases.length; index++) {
      if (!unit.entry.canvases[index]) continue;
      const page = unit.pages.find((p: any) => p.index === index);
      const parsed = page ? native.correctedPage(page.text, unitKey, index + 1, decisions) : { halves: [] };
      const items: any[] = [];
      if (unit.source.kind === "wordlist") for (const half of parsed.halves) {
        const result = parseSourceWordlist(unit.source.slug, unit.slug, index + 1, half, carry);
        carry = result.carry;
        items.push(...result.items);
      }
      // An entry target on a prose page also fails here instead of silently disappearing.
      native.validateEntryCorrections(unitKey, index + 1, items, decisions);
    }
  }
  const files = [...fileRecords].sort(([a], [b]) => a.localeCompare(b)).map(([path, additions]) => {
    const before = beforeFiles.get(path)!;
    if (fileText(join(root, path)) !== before) fail(`Source corrections changed during validation: ${path}`);
    const records = before === null ? [] : JSON.parse(before);
    if (!Array.isArray(records)) fail(`Invalid existing correction file: ${path}`);
    // Do not serialize the native loader's normalized records back over the author's spelling.
    return { path, before, before_sha256: before === null ? null : digest(before),
      after: JSON.stringify([...records, ...additions], null, 2) + "\n" };
  });
  let sourceRevision: string | null = null;
  try { sourceRevision = execFileSync("git", ["-C", root, "rev-parse", "HEAD"],
    { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 5000 }).trim(); } catch {}
  return { version: 1, validated: true, source_revision: sourceRevision, files, proposals: resolved, conflicts: [] };
}

try {
  const output = await prepare(process.argv[2], await Bun.stdin.json());
  process.stdout.write(JSON.stringify(output));
} catch (error) {
  process.stdout.write(JSON.stringify({ version: 1, validated: false, files: [], proposals: [],
    conflicts: [{ id: null, reason: String((error as Error).message) }] }));
}
