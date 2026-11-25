#!/usr/bin/env bun
/**
 * Sequence occurrence review in a real browser against a stateful mock API:
 * 1. Clicking a sequence tile on Explore opens that occurrence in context without navigating away,
 *    and offers a "more" link to the sequence page (`/sequence/<text>`).
 * 2. Inside `RunOccurrence`, characters can be approved inline one by one (local & corpus), moving
 *    focus to the next character, or opened in the crop inspector to fix one by one, updating the
 *    occurrence row, progress count, and underlying tile/card state immediately.
 * 3. Following the "more" link opens `/sequence/<text>` (`RunView`), where stepping between
 *    occurrences and approving/fixing characters updates the sequence grid cards.
 *
 * Run:
 *   bun apps/review/tools/run-review-check.mjs
 */
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import Browser from './browser.mjs'

const HERE = import.meta.dir
const ROOT = resolve(HERE, '..', '..', '..')
const APP = join(ROOT, 'apps', 'review')

// 1x1 transparent PNG so `<img onload>` fires synchronously with valid naturalWidth/Height.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=',
  'base64',
)

const assert = (condition, message) => { if (!condition) throw new Error(message) }

function freePort() {
  const probe = Bun.listen({ hostname: '127.0.0.1', port: 0, socket: { data() {} } })
  const { port } = probe
  probe.stop(true)
  return port
}

function createState() {
  const localCrops = {
    'local-run-0': {
      id: 'local-run-0', origin: 'local', label: 'は', char: 'は', written_character: 'は',
      code_point: 'U+306F', grapheme: 'は', script: 'hiragana', style: 'cursive', state: 'unchecked',
      issue: null, revision: 1, image_sha256: 'sha-local-run-0', image: '/images/crop-0.png',
      context: true, context_image: '/images/context-1.png', context_box: { x: 0, y: 0, w: 400, h: 400 },
      crop_box: { x: 140, y: 60, w: 70, h: 70 }, crop_editable: true, source_scale: [1, 1],
      source_id: 'doc-1', page_id: 'p-1', page_number: 12, dating: {},
      source: { title: 'Test Manuscript', provider: 'ndl', date: '1800', url: 'https://example.org/p12', source_url: 'https://example.org/work' },
      style_choices: ['cursive', 'regular'], suggestions: [{ text: 'は', script: 'hiragana', score: 0.9 }],
    },
    'local-run-1': {
      id: 'local-run-1', origin: 'local', label: 'の', char: 'の', written_character: 'の',
      code_point: 'U+306E', grapheme: 'の', script: 'hiragana', style: 'cursive', state: 'unchecked',
      issue: null, revision: 1, image_sha256: 'sha-local-run-1', image: '/images/crop-1.png',
      context: true, context_image: '/images/context-1.png', context_box: { x: 0, y: 0, w: 400, h: 400 },
      crop_box: { x: 140, y: 140, w: 70, h: 70 }, crop_editable: true, source_scale: [1, 1],
      source_id: 'doc-1', page_id: 'p-1', page_number: 12, dating: {},
      source: { title: 'Test Manuscript', provider: 'ndl', date: '1800', url: 'https://example.org/p12', source_url: 'https://example.org/work' },
      style_choices: ['cursive', 'regular'], suggestions: [{ text: 'な', script: 'hiragana', score: 0.92 }],
    },
  }

  const corpusCrops = {
    'corpus-run-0': {
      id: 'corpus-run-0', origin: 'corpus', label: 'は', written_character: 'は', source_label: 'は',
      grapheme: 'は', script: 'hiragana', style: 'cursive', state: 'unchecked', issue: null,
      revision: 1, source_revision: 1, proxyable: true, identity_status: 'matched', needs_segmentation: false,
      image: '/images/crop-2.png', context: true, context_image: '/images/context-2.png',
      context_box: { x: 0, y: 0, w: 400, h: 400 }, context_basis: 'derived_viewport',
      crop_box: { x: 150, y: 70, w: 68, h: 68 }, crop_editable: false,
      source_id: 'corp-1', page_id: 'p-2', page_number: 34, dating: {},
      source: { title: 'Corpus Edition', provider: 'ndl', date: '1790', url: 'https://example.org/corp34', source_url: 'https://example.org/corp' },
      family_members: ['は', '者'], suggestions: [{ text: 'は', script: 'hiragana', score: 0.9 }],
    },
    'corpus-run-1': {
      id: 'corpus-run-1', origin: 'corpus', label: 'の', written_character: 'の', source_label: 'の',
      grapheme: 'の', script: 'hiragana', style: 'cursive', state: 'unchecked', issue: null,
      revision: 1, source_revision: 1, proxyable: true, identity_status: 'matched', needs_segmentation: false,
      image: '/images/crop-3.png', context: true, context_image: '/images/context-2.png',
      context_box: { x: 0, y: 0, w: 400, h: 400 }, context_basis: 'derived_viewport',
      crop_box: { x: 150, y: 145, w: 68, h: 68 }, crop_editable: false,
      source_id: 'corp-1', page_id: 'p-2', page_number: 34, dating: {},
      source: { title: 'Corpus Edition', provider: 'ndl', date: '1790', url: 'https://example.org/corp34', source_url: 'https://example.org/corp' },
      family_members: ['の', 'な'], suggestions: [{ text: 'な', script: 'hiragana', score: 0.95 }],
    },
  }

  const galleryItems = Array.from({ length: 84 }, (_, i) => ({
    id: `gal-${i}`, origin: 'local', label: i % 2 === 0 ? 'は' : 'の', char: i % 2 === 0 ? 'は' : 'の',
    code_point: i % 2 === 0 ? 'U+306F' : 'U+306E', script: 'hiragana', style: 'cursive', state: 'unchecked',
    revision: 1, image_sha256: `sha-gal-${i}`, image: `/images/gal-${i}.png`,
    aspect: 1, source_id: 'doc-1', page_number: i + 1, source: { title: 'Test Manuscript' },
  }))

  const occurrences = [
    {
      cropIds: ['local-run-0', 'local-run-1'],
      vertical: true,
      page: { image: '/images/context-1.png', box: { x: 0, y: 0, w: 400, h: 400 }, region: { x: 110, y: 35, w: 130, h: 200 }, cut: null },
      honkoku_url: null,
    },
    {
      cropIds: ['corpus-run-0', 'corpus-run-1'],
      vertical: true,
      page: { image: '/images/context-2.png', box: { x: 0, y: 0, w: 400, h: 400 }, region: { x: 120, y: 45, w: 130, h: 200 }, cut: null },
      honkoku_url: null,
    },
  ]

  const requests = []

  const cropById = id => localCrops[id] ?? corpusCrops[id] ?? galleryItems.find(item => item.id === id) ?? null
  const resolveOccurrence = occ => ({ ...occ, crops: occ.cropIds.map(id => structuredClone(cropById(id))) })

  return { localCrops, corpusCrops, galleryItems, occurrences, requests, cropById, resolveOccurrence }
}

function startMockApi(state) {
  const server = Bun.serve({
    hostname: '127.0.0.1',
    port: 0,
    async fetch(req) {
      const url = new URL(req.url)
      const path = url.pathname

      if (path.startsWith('/images/')) {
        return new Response(PNG, { headers: { 'content-type': 'image/png', 'cache-control': 'no-store' } })
      }
      if (path === '/health') return Response.json({ status: 'ok' })
      if (path === '/atlas/collection/status') return Response.json({ archive: { character_crops: state.galleryItems.length + 2, works: 2 } })
      if (path === '/atlas/groups/stats') return Response.json({ groups: [{ group: 'all', total: 86, reviewed: 0, need_fixing: 0 }] })
      if (path === '/atlas/forms') return Response.json({ families: [] })
      if (path === '/pages') return Response.json({ detail: 'not found' }, { status: 404 })
      if (path === '/atlas/graphemes/counts') return Response.json({ items: [] })
      if (path === '/layers/gallery') return Response.json({ status: 'ok', items: [] })

      if (path === '/atlas' && url.searchParams.get('purpose') === 'browse') {
        return Response.json({
          total: state.galleryItems.length,
          available: state.galleryItems.length,
          items: state.galleryItems,
          categories: ['hiragana'],
          documents: [{ id: 'doc-1', title: 'Test Manuscript' }],
          counts: { checked: 0, flagged: 0, pending: state.galleryItems.length },
          next: null,
          reported_count: 0,
          withheld_count: 0,
        })
      }

      if (path === '/atlas/ngrams/2' || path === '/atlas/ngrams/3') {
        return Response.json({
          items: path.endsWith('/2') ? [{ text: 'はの', n: 2, vertical: true, forms: 1 }] : [],
          limit: 480,
        })
      }

      if (path === '/atlas/runs') {
        const limit = Number(url.searchParams.get('limit') ?? 24)
        const offset = Number(url.searchParams.get('offset') ?? 0)
        // `Explore.scatter` picks a random offset up to n - 1 when requesting `limit=1`; serve occurrence 0 there.
        const items = limit === 1
          ? [state.resolveOccurrence(state.occurrences[0])]
          : state.occurrences.slice(offset, offset + limit).map(state.resolveOccurrence)
        return Response.json({
          text: url.searchParams.get('text') ?? 'はの',
          size: 2,
          document: null,
          form: null,
          hand: 'all',
          sort: 'hand',
          next_offset: offset + items.length,
          total: state.occurrences.length,
          more: false,
          vertical: true,
          hands: { hand: state.occurrences.length, unknown: 0, type: 0 },
          hand_groups: ['hand', 'unknown', 'type'],
          works: [{ id: 'doc-1', title: 'Test Manuscript', n: 1 }],
          forms: [{ text: 'はの', n: 2 }],
          items,
        })
      }

      if (path === '/atlas/runs/related') {
        return Response.json({ text: url.searchParams.get('text') ?? 'はの', size: 2, lead: [], inside: [], longer: [], siblings: [], limit: 12 })
      }

      const localSugMatch = path.match(/^\/atlas\/characters\/([^/]+)\/suggestions$/)
      if (localSugMatch && req.method === 'GET') {
        const crop = state.cropById(decodeURIComponent(localSugMatch[1]))
        return Response.json({ status: 'ready', candidates: crop?.suggestions ?? [] })
      }

      const localLineMatch = path.match(/^\/atlas\/characters\/([^/]+)\/line$/)
      if (localLineMatch && req.method === 'GET') {
        return Response.json({ id: decodeURIComponent(localLineMatch[1]), reach: 4, items: [] })
      }

      const localSimMatch = path.match(/^\/atlas\/characters\/([^/]+)\/similar$/)
      if (localSimMatch && req.method === 'GET') {
        return Response.json({ revision: 1, similar: [], filed_differently: [] })
      }

      const localCharMatch = path.match(/^\/atlas\/characters\/([^/]+)$/)
      if (localCharMatch && req.method === 'GET') {
        const crop = state.cropById(decodeURIComponent(localCharMatch[1]))
        return crop ? Response.json(crop) : Response.json({ detail: 'not found' }, { status: 404 })
      }

      if (localCharMatch && req.method === 'POST') {
        const id = decodeURIComponent(localCharMatch[1])
        const body = await req.json()
        state.requests.push({ path, body })
        const crop = state.cropById(id)
        if (!crop) return Response.json({ detail: 'not found' }, { status: 404 })
        if (body.revision !== crop.revision) return Response.json({ detail: 'revision mismatch' }, { status: 409 })
        crop.revision += 1
        crop.state = body.verdict === 'match' ? 'checked' : 'flagged'
        crop.issue = body.verdict === 'match' ? null : (body.issue ?? null)
        return Response.json({ id, results: [{ id: state.requests.length, target_id: id, field: 'review', revision: crop.revision, state: crop.state }] })
      }

      const unitMatch = path.match(/^\/layers\/units\/([^/]+)$/)
      if (unitMatch && req.method === 'POST') {
        const id = decodeURIComponent(unitMatch[1])
        const body = await req.json()
        state.requests.push({ path, body })
        const crop = state.cropById(id)
        if (!crop) return Response.json({ detail: 'not found' }, { status: 404 })
        if (body.revision !== crop.revision) return Response.json({ detail: 'revision mismatch' }, { status: 409 })
        crop.revision += 1
        if (body.character) {
          crop.label = body.character
          crop.char = body.character
          crop.written_character = body.character
          crop.code_point = 'U+' + body.character.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')
        }
        crop.state = body.verdict === 'match' || body.character ? 'checked' : 'flagged'
        crop.issue = crop.state === 'checked' ? null : (body.issue ?? null)
        return Response.json({ id, results: [{ id: state.requests.length, target_id: id, field: 'review', revision: crop.revision, state: crop.state }] })
      }

      return Response.json({ detail: `unhandled mock route: ${req.method} ${path}` }, { status: 404 })
    },
  })
  return { server, base: `http://127.0.0.1:${server.port}` }
}

async function startWeb(apiBase) {
  if (!existsSync(join(APP, '.svelte-kit', 'output'))) {
    throw new Error('apps/review has no build; run `bun run --cwd apps/review build` first')
  }
  const webPort = freePort()
  const base = `http://127.0.0.1:${webPort}`
  const log = []
  const proc = Bun.spawn(
    ['bunx', 'vite', 'preview', '--port', String(webPort), '--strictPort', '--host', '127.0.0.1'],
    { cwd: APP, env: { ...process.env, ATLAS_REVIEW_API: apiBase }, stdout: 'pipe', stderr: 'pipe' },
  )
  for (const stream of [proc.stdout, proc.stderr]) {
    ;(async () => { for await (const chunk of stream) log.push(new TextDecoder().decode(chunk)) })()
  }
  const deadline = Date.now() + 30000
  while (Date.now() < deadline) {
    if (proc.exitCode !== null) throw new Error(`vite preview exited (${proc.exitCode}):\n${log.join('')}`)
    try {
      if ((await fetch(`${base}/en`)).status === 200) return { base, proc, log }
    } catch { /* wait until listening */ }
    await Bun.sleep(150)
  }
  throw new Error(`vite preview did not start within 30s:\n${log.join('')}`)
}

const state = createState()
const api = startMockApi(state)
let web, browser
const exceptions = []

async function clickEl(selector) {
  await browser.evaluate(`document.querySelector(${JSON.stringify(selector)})?.scrollIntoView({ block: 'center', inline: 'nearest' })`)
  await Bun.sleep(40)
  const at = await browser.centre(selector)
  await browser.click(at.x, at.y)
}

try {
  web = await startWeb(api.base)
  browser = await Browser.launch({ width: 1440, height: 900 })
  await browser.writeAs('reviewer-seq-test')
  browser.listeners.push(msg => {
    if (msg.method === 'Runtime.exceptionThrown') exceptions.push(msg.params.exceptionDetails?.text ?? 'exception')
  })

  // 1. Explore -> open sequence tile in context without navigating away.
  await browser.goto(`${web.base}/en`, { waitFor: 'document.querySelector(".glyph-grid button.run-tile") !== null', timeout: 60000 })
  await browser.waitFor('document.documentElement.dataset.hydrated !== undefined')
  const explorePathBefore = await browser.evaluate('location.pathname')
  assert(explorePathBefore === '/en', `expected /en, got ${explorePathBefore}`)

  await clickEl('.glyph-grid button.run-tile')
  await browser.waitFor('document.querySelector(".run-occurrence-dialog[open]") !== null')
  assert(await browser.evaluate('location.pathname') === '/en', 'clicking a run tile on Explore keeps /en in the address bar')
  assert(
    await browser.evaluate('document.querySelectorAll(".run-occurrence-dialog .occurrence-crop-row").length') === 2,
    'occurrence dialog lists both character crops',
  )
  const seqHref = await browser.evaluate('document.querySelector(".run-occurrence-dialog .run-sequence-link")?.getAttribute("href")')
  assert(seqHref === '/en/sequence/' + encodeURIComponent('はの'), `unexpected sequence link href: ${seqHref}`)
  const seqCount = await browser.evaluate('document.querySelector(".run-occurrence-dialog .run-sequence-link small")?.textContent')
  assert(seqCount?.includes('2'), `expected sequence link count including "2", got ${JSON.stringify(seqCount)}`)
  assert(await browser.evaluate('document.querySelector(".run-occurrence-dialog .occurrence-page svg.run-image") !== null'), 'occurrence dialog renders context RunImage')
  console.log('PASS Explore opens sequence occurrence in context with "more" link and keeps URL')

  // 2. Inline approve character 0 -> moves focus to character 1's approve button and updates progress.
  await clickEl('.run-occurrence-dialog .occurrence-crop-row:nth-child(1) .crop-approve')
  await browser.waitFor('document.querySelector(".run-occurrence-dialog .occurrence-crop-row:nth-child(1)")?.classList.contains("checked")')
  const post0 = state.requests.at(-1)
  assert(post0?.path === '/atlas/characters/local-run-0' && post0?.body?.verdict === 'match', `expected match POST for local-run-0, got ${JSON.stringify(post0)}`)
  const progress1 = await browser.evaluate('document.querySelector(".run-occurrence-dialog .occurrence-chars-head span")?.textContent')
  assert(progress1?.includes('1 / 2'), `expected progress 1 / 2, got ${progress1}`)
  assert(
    await browser.evaluate('document.activeElement?.closest(".occurrence-crop-row") === document.querySelector(".run-occurrence-dialog .occurrence-crop-row:nth-child(2)")'),
    'approving char 0 moves focus to the next unapproved character row',
  )
  console.log('PASS inline approval updates crop state, progress, and advances focus')

  // 2b. Reopen inline-approved local crop via Inspect to ensure remembered decision shape is valid.
  await clickEl('.run-occurrence-dialog .occurrence-crop-row:nth-child(1) .crop-fix')
  await browser.waitFor('document.querySelector(".character-dialog[open]:not(.run-occurrence-dialog) .crop-review") !== null')
  assert(await browser.evaluate('document.querySelector(".character-dialog .inspector-navigation")?.textContent.includes("1 / 2")'), 'Inspect opens on the approved crop at 1 / 2')
  await clickEl('.character-dialog .close-inspector')
  await browser.waitFor('document.querySelector(".character-dialog[open]:not(.run-occurrence-dialog)") === null')
  assert(exceptions.length === 0, `unexpected runtime exceptions after Inspect reopen: ${exceptions.join('; ')}`)

  // 3. Fix character 1 via the shared CharacterDialog opened from RunOccurrence.
  await clickEl('.run-occurrence-dialog .occurrence-crop-row:nth-child(2) .crop-fix')
  await browser.waitFor('document.querySelector(".character-dialog[open]:not(.run-occurrence-dialog)") !== null')
  await browser.waitFor('document.querySelector(".character-dialog[open]:not(.run-occurrence-dialog) .crop-review") !== null')
  assert(await browser.evaluate('document.querySelectorAll("dialog[open]").length') === 2, 'CharacterDialog stacks over RunOccurrence')
  assert(await browser.evaluate('document.querySelector(".character-dialog .inspector-navigation")?.textContent.includes("2 / 2")'), 'inspector queues over the 2 occurrence crops starting at 2 / 2')

  await clickEl('.character-dialog .issue-card[data-issue="character"]')
  await browser.waitFor('document.querySelector(".character-dialog .suggestion-choice button") !== null')
  await clickEl('.character-dialog .suggestion-choice button')
  await browser.waitFor('document.querySelector(".character-dialog .save-character") && !document.querySelector(".character-dialog .save-character").disabled')
  await clickEl('.character-dialog .save-character')

  await browser.waitFor('document.querySelector(".character-dialog[open]:not(.run-occurrence-dialog)") === null')
  await browser.waitFor('document.querySelector(".run-occurrence-dialog[open]") !== null')
  await browser.waitFor('document.querySelector(".run-occurrence-dialog .occurrence-crop-row:nth-child(2)")?.classList.contains("checked")')

  const post1 = state.requests.at(-1)
  assert(post1?.path === '/layers/units/local-run-1' && post1?.body?.character === 'な', `expected character fix POST for local-run-1, got ${JSON.stringify(post1)}`)
  assert(
    await browser.evaluate('document.querySelector(".run-occurrence-dialog .occurrence-crop-row:nth-child(2) .crop-written .reference-glyph")?.textContent?.trim()') === 'な',
    'fixed crop row immediately reflects the updated character "な"',
  )
  assert(
    await browser.evaluate('document.querySelector(".run-occurrence-dialog .occurrence-crop-row:nth-child(2) .crop-written .crop-grapheme")?.textContent?.trim()') === 'の',
    'fixed crop row keeps the sequence grapheme "の" beside the updated character',
  )
  assert(
    await browser.evaluate('document.querySelector(".run-occurrence-dialog .occurrence-chars-head span")?.textContent?.includes("2 / 2")'),
    'progress reaches 2 / 2 after fixing the second character',
  )
  assert(await browser.evaluate('document.querySelector(".glyph-grid .run-tile")?.classList.contains("decided-checked")'), 'Explore run tile behind dialog reflects decided-checked')
  assert(await browser.evaluate('document.querySelector(".glyph-grid .run-tile")?.textContent?.includes("はな")'), 'Explore run tile reflects updated written text "はな"')
  console.log('PASS fixing a character in CharacterDialog updates RunOccurrence and Explore tile immediately')

  // 4. Follow "more" link from RunOccurrence to `/en/sequence/...` (`RunView`).
  await clickEl('.run-occurrence-dialog .run-sequence-link')
  await browser.waitFor('location.pathname === "/en/sequence/" + encodeURIComponent("はの")')
  await browser.waitFor('document.querySelectorAll(".run-grid .run-occurrence").length === 2')
  assert(await browser.evaluate('document.querySelector("dialog[open]") === null'), 'navigating via "more" closes the dialog cleanly')
  console.log('PASS clicking "more" navigates from occurrence dialog to /sequence/<text>')

  // 5. In RunView, open occurrence 1, step to occurrence 2 (corpus crops), approve & fix one by one.
  await clickEl('.run-grid .run-occurrence:nth-child(1) .run-open')
  await browser.waitFor('document.querySelector(".run-occurrence-dialog[open]") !== null')
  assert(await browser.evaluate('document.querySelector(".run-occurrence-dialog .previous-occurrence")?.disabled') === true, 'previous disabled at 1 / 2')
  assert(await browser.evaluate('document.querySelector(".run-occurrence-dialog .occurrence-nav > span")?.textContent') === '1 / 2', 'position shows 1 / 2')

  await clickEl('.run-occurrence-dialog .next-occurrence')
  await browser.waitFor('document.querySelector(".run-occurrence-dialog .occurrence-nav > span")?.textContent === "2 / 2"')

  // Approve corpus crop 0 inline
  await clickEl('.run-occurrence-dialog .occurrence-crop-row:nth-child(1) .crop-approve')
  await browser.waitFor('document.querySelector(".run-occurrence-dialog .occurrence-crop-row:nth-child(1)")?.classList.contains("checked")')
  const corpPost0 = state.requests.at(-1)
  assert(corpPost0?.path === '/atlas/characters/corpus-run-0' && corpPost0?.body?.verdict === 'match' && corpPost0?.body?.source_revision !== undefined, `expected corpus match POST, got ${JSON.stringify(corpPost0)}`)

  // Fix corpus crop 1 in the same CharacterDialog a collection crop opens in
  await clickEl('.run-occurrence-dialog .occurrence-crop-row:nth-child(2) .crop-fix')
  await browser.waitFor('document.querySelector(".character-dialog[open]:not(.run-occurrence-dialog) .crop-review") !== null')
  await clickEl('.character-dialog .issue-card[data-issue="character"]')
  await browser.waitFor('document.querySelector(".character-dialog .suggestion-choice button") !== null')
  await clickEl('.character-dialog .suggestion-choice button')
  await browser.waitFor('document.querySelector(".character-dialog .save-character") && !document.querySelector(".character-dialog .save-character").disabled')
  await clickEl('.character-dialog .save-character')

  await browser.waitFor('document.querySelector(".character-dialog[open]:not(.run-occurrence-dialog)") === null')
  await browser.waitFor('document.querySelector(".run-occurrence-dialog .occurrence-crop-row:nth-child(2)")?.classList.contains("checked")')
  const corpPost1 = state.requests.at(-1)
  assert(corpPost1?.path === '/layers/units/corpus-run-1', `expected character fix POST for corpus-run-1, got ${JSON.stringify(corpPost1)}`)

  // Verify phone viewport has no horizontal overflow inside open RunOccurrence.
  await browser.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
  await Bun.sleep(100)
  const overflow = await browser.evaluate('(() => { const d = document.querySelector(".run-occurrence-dialog[open]"); return d ? d.scrollWidth - d.clientWidth : 0 })()')
  assert(overflow <= 1, `mobile RunOccurrence should not horizontally overflow (overflow=${overflow})`)

  await clickEl('.run-occurrence-dialog .close-occurrence')
  await browser.waitFor('document.querySelector(".run-occurrence-dialog[open]") === null')
  assert(await browser.evaluate('document.querySelector(".run-grid .run-occurrence:nth-child(2)")?.classList.contains("decided-checked")'), 'RunView occurrence card 2 shows decided-checked')
  assert(await browser.evaluate('document.querySelector(".run-grid .run-occurrence:nth-child(2) .run-forms")?.textContent?.includes("はな")'), 'RunView occurrence card 2 reflects fixed text "はな"')
  console.log('PASS RunView stepping, corpus inline/inspector review, card updates, and mobile layout')

  assert(exceptions.length === 0, `unexpected browser runtime exceptions: ${exceptions.join('; ')}`)
  console.log('PASS run-review-check completed cleanly with zero runtime exceptions')
} finally {
  await browser?.close()
  if (web?.proc && web.proc.exitCode === null) {
    web.proc.kill()
    await web.proc.exited.catch(() => {})
  }
  api.server.stop(true)
}
