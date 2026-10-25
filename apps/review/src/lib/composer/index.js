// The composer in the browser: han-compose's export (manifest, layout and the drawing data of each
// key) loaded as a sequence needs it, and the sequence drawn from it as an SVG path. A file is
// fetched once a page; a gzipped one (the site stores them so) is unpacked with DecompressionStream.
import { APPROXIMATE, Composer, EMPTY, LookupError, keyOf, parseTree } from './compose.js'
import { ASCENT } from './geometry.js'
import { svgPath } from './geometry.js'
import { readLayout } from './layout.js'
import { bucketOf, hexOf } from './sha256.js'

export { LookupError } from './compose.js'

async function unpack(buffer) {
  const head = new Uint8Array(buffer, 0, 2)
  if (head[0] !== 0x1f || head[1] !== 0x8b) return new TextDecoder().decode(buffer)
  const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'))
  return await new Response(stream).text()
}

// Where the export keeps each key's data, by its format (`manifest.format`): the one place that knows
// file names. Format 1 holds every key in one of 256 buckets, `b/<first byte of sha256(key)>.json`,
// its host pieces by place under the record's `x`. Format 2 gives each key a file of its own,
// `g/<hh>/<sha256(key)>.json`, and each (place, operand) its host list, `p/<hh>/<sha256(place TAB
// operand)>.json`; a record lists the places it has lists in (`P`, absent when it has none). Those
// files are gzip bytes under a .json name, served as application/octet-stream and unpacked here.
const hashed = (folder, text) => { const h = hexOf(text); return `${folder}/${h.slice(0, 2)}/${h}.json` }
const bucket = key => `b/${String(bucketOf(key)).padStart(3, '0')}.json`
const FORMATS = {
  1: {
    recordFile: bucket,
    hostsFiles: key => [bucket(key)],
    hostsFile: key => bucket(key),
    record: (file, key) => file[key] ?? null,
    hosts: (file, key, place) => file[key]?.x?.[place] ?? [],
    places: record => Object.keys(record?.x ?? {}),
  },
  2: {
    recordFile: key => hashed('g', key),
    hostsFiles: () => [],
    hostsFile: (key, place) => hashed('p', `${place}\t${key}`),
    record: file => Object.keys(file).length ? file : null,
    hosts: file => file.hosts ?? [],
    places: record => record?.P ?? [],
  },
}
// Format 3 keeps format 2's files, and its host lists hold every entry of the composer's index with
// the fields it ranks by; an entry's cut `c` is null where the composer could not cut it, else the
// indices of the host glyph's contours it keeps with their box, read here as `k` and `b`. The
// outlines come from the host's own record when a piece is chosen.
const cutEntry = h => h.c === undefined || h.k ? h
  : h.c === null ? { ...h, c: null } : { ...h, k: Array.isArray(h.c) ? h.c : h.c.k, b: Array.isArray(h.c) ? h.b : h.c.b }
FORMATS[3] = { ...FORMATS[2], hosts: file => (file.hosts ?? []).map(cutEntry) }

/**
 * A source of the export's files. `read(name)` answers a file's bytes (an ArrayBuffer) by its name
 * in the export (`manifest.json`, `layout.json`, `b/007.json`), or null where there is no such file;
 * `fromUrl` builds one over HTTP.
 */
export function createSource(read, { concurrency = 6 } = {}) {
  let ready = null, format = null
  // At most `concurrency` files are asked for at once; the rest wait their turn.
  let active = 0
  const waiting = []
  const limited = name => new Promise((resolve, reject) => {
    const run = () => {
      active++
      read(name).then(resolve, reject).finally(() => { active--; waiting.shift()?.() })
    }
    if (active < concurrency) run()
    else waiting.push(run)
  })
  // Host lists are read into the composer's order once a page, so their pieces are decoded once.
  const files = new Map(), loading = new Map(), lists = new Map()
  const source = {
    manifest: null, layout: null,
    ready() {
      ready ??= (async () => {
        source.manifest = JSON.parse(await unpack(await read('manifest.json')))
        format = FORMATS[source.manifest.format]
        if (!format) throw new Error(`The composer export's format ${source.manifest.format} is not read here.`)
        source.layout = readLayout(JSON.parse(await unpack(await read(source.manifest.files.layout.path))))
      })().catch(error => { ready = null; throw error })
      return ready
    },
    /** Loads files by name, each once; a file the export lacks reads as empty. */
    load(names) {
      return Promise.all([...names].map(name => {
        if (files.has(name)) return null
        if (!loading.has(name)) loading.set(name, limited(name).then(async bytes => {
          files.set(name, bytes ? JSON.parse(await unpack(bytes)) : {})
        }).finally(() => loading.delete(name)))
        return loading.get(name)
      }))
    },
    /** The files holding the records of `keys`, to load before drawing. */
    filesOf: keys => new Set([...keys].flatMap(key => [format.recordFile(key), ...format.hostsFiles(key)])),
    /** The data a composer reads, noting each file it asked for that is not loaded. */
    store() {
      const missing = new Set()
      const file = name => { const found = files.get(name); if (!found) missing.add(name); return found ?? null }
      return {
        manifest: source.manifest, layout: source.layout, missing,
        rec: key => { const f = file(format.recordFile(key)); return f ? format.record(f, key) : null },
        // A host list in the order of the composer's index (`r`), where the export gives it.
        hosts: (key, place) => {
          const f = file(format.hostsFile(key, place)), id = `${place}\t${key}`
          if (!f) return []
          if (!lists.has(id)) {
            const list = format.hosts(f, key, place)
            lists.set(id, list[0]?.r === undefined ? list : list.slice().sort((a, b) => a.r - b.r))
          }
          return lists.get(id)
        },
        places: key => {
          const f = file(format.recordFile(key))
          return f ? format.places(format.record(f, key)) : []
        },
      }
    },
  }
  return source
}

/** A source over HTTP: `base` is the folder holding the export, `suffix` what each name carries there (`.gz`). */
export function fromUrl(base, { suffix = '', fetch: get = globalThis.fetch } = {}) {
  const root = base.endsWith('/') ? base : `${base}/`
  return createSource(async name => {
    const response = await get(`${root}${name}${suffix}`)
    if (response.status === 404 && name !== 'manifest.json') return null
    if (!response.ok) throw new Error(`${name}: ${response.status}`)
    return await response.arrayBuffer()
  })
}

const ROUNDS = 16
/** The largest description drawn: more operators, or deeper nesting, than any character needs, and
 *  more work than a preview can spend. */
export const LIMITS = { nodes: 12, depth: 4 }

/** How many operators a tree has, and how deep they nest: a parsed tree (['⿰', …]) or the editor's
 *  (`{ op, parts }`). */
export function size(tree) {
  const kids = Array.isArray(tree) ? tree.slice(1) : tree && typeof tree === 'object' ? tree.parts : null
  if (!kids) return { nodes: 0, depth: 0 }
  const sizes = kids.map(size)
  return { nodes: 1 + sizes.reduce((s, k) => s + k.nodes, 0), depth: 1 + Math.max(0, ...sizes.map(k => k.depth)) }
}
/** Whether a tree is larger than a preview draws (`LIMITS`). */
export const tooLarge = tree => { const { nodes, depth } = size(tree); return nodes > LIMITS.nodes || depth > LIMITS.depth }

/** Every key the tree names, nodes and leaves, as the first files to load; none holding an empty slot. */
function keysOf(tree, out = []) {
  if (!keyOf(tree).includes(EMPTY)) out.push(keyOf(tree))
  if (Array.isArray(tree)) tree.slice(1).forEach(n => keysOf(n, out))
  return out
}

/** A box in font units (y up) as the drawing's SVG box has it: [x, y, width, height], y down. */
export const svgBox = b => [b[0], ASCENT - b[3], b[2] - b[0], b[3] - b[1]]

/**
 * Where each leaf of `sequence` goes before it is drawn (`Composer.sketch`), once the manifest and the
 * layout have loaded: `[{ index, box, empty }]`, boxes as `svgBox` gives them.
 */
export async function sketch(source, sequence) {
  await source.ready()
  return new Composer(source.store()).sketch(sequence).map(leaf => ({ ...leaf, box: svgBox(leaf.box) }))
}

/**
 * `sequence` drawn by the composer: `{ d, cut, slots, stroke, parts, origins, kanjivg, approximate }`,
 * `d` the SVG path of its outlines in a 1000-unit box (y down, nonzero fill), `cut` the path of what
 * ㇯ takes away (empty when nothing is), `slots` each empty slot (＋) as `{ index, box }`, numbered in
 * reading order, in the box the layout gave it (as `svgBox` gives it), `stroke` the stroke widths
 * the drawing is weighted to, across x and y, `parts` each drawn part's origin and box, `origins`
 * where each part came from, `kanjivg` whether a part was cut along KanjiVG's groups or a hand form
 * read from it, and `approximate` the operators it uses that the composer itself does not draw. `exclude` lists characters nothing is taken from, and `face` the box
 * a whole character fills without them. Throws LookupError when a part can be drawn no way or the
 * sequence is larger than `LIMITS`.
 */
export async function compose(source, sequence, { exclude = [], face = null } = {}) {
  await source.ready()
  const arity = ch => {
    const c = source.manifest.constants
    return c.BINARY.includes(ch) ? 2 : c.TERNARY.includes(ch) ? 3 : c.UNARY.includes(ch) ? 1 : 0
  }
  const tree = parseTree(sequence, arity)
  if (tooLarge(tree)) throw new LookupError(`${sequence} is larger than a preview draws.`)
  await source.load(source.filesOf(keysOf(tree)))
  for (let round = 0; round < ROUNDS; round++) {
    const store = source.store()
    let drawn = null, failure = null, composer = null
    try { composer = new Composer(store, { exclude, face }); drawn = composer.compose(sequence) } catch (error) { failure = error }
    // A pass that met data not loaded yet only gathers what to load (`Composer.partial`); the drawing
    // is made once all of it is here.
    if (store.missing.size) { await source.load(store.missing); continue }
    if (failure) throw failure
    const ink = drawn.filter(p => p.empty === undefined)
    return { d: svgPath(ink.filter(p => !p.cut)), cut: svgPath(ink.filter(p => p.cut)),
      slots: drawn.filter(p => p.empty !== undefined).map(p => ({ index: p.empty, box: svgBox(p.target) })),
      stroke: composer.stroke ?? null, parts: ink.map(p => ({ origin: p.origin, box: svgBox(p.target) })), origins: ink.map(p => p.origin),
      kanjivg: Boolean(composer.handForms) || drawn.some(p => p.part.kv), approximate: [...new Set([...sequence].filter(ch => APPROXIMATE.includes(ch)))] }
  }
  throw new LookupError(`${sequence} needs more data than ${ROUNDS} rounds load.`)
}
