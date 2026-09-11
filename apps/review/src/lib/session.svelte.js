/**
 * The session: what is loaded, what is selected, and what the keys do.
 *
 * One `Session` instance is created by `App.svelte` and passed down to the views. It owns the calls
 * to the T40 service, the selection model, the client's undo stack, the 409 dialog and the timing
 * events. Views are told where they are through the hash route (`#/queue`, `#/page/<id>`,
 * `#/line/<id>`), so a line can be linked to and a screenshot taken of a named view.
 */

import { api, ApiError, ConflictError } from './api.js'
import { normalizeBox, orderUnits, splitBox, unionBox } from './geometry.js'

const CLIENT_KEY = 'atlas.review.client_id'
const THEME_KEY = 'atlas.review.theme'
const UNDO_KEY = 'atlas.review.undo'

/** A client id that names the reviewer; two reviewers must not share one. */
export function freshClientId() {
  return `reviewer-${Math.random().toString(36).slice(2, 8)}`
}

function stored(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}

function remember(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* a private window keeps the value for the session only */
  }
}

/** A short unique key, so that a retried request is not recorded twice. */
function key() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
}

export class Session {
  // -- the service ---------------------------------------------------------------------------
  status = $state('loading') // loading | ready | error
  error = $state(null)
  busy = $state(false)

  // -- who is reviewing ----------------------------------------------------------------------
  clientId = $state(stored(CLIENT_KEY, freshClientId()))
  theme = $state(stored(THEME_KEY, 'system'))

  // -- the queue -----------------------------------------------------------------------------
  documents = $state([])
  strategy = $state('unreviewed')
  documentFilter = $state('')
  queue = $state({ items: [], total: 0, limit: 50, offset: 0, loading: false })

  // -- the open records ----------------------------------------------------------------------
  route = $state({ name: 'queue', id: null })
  page = $state(null)
  lines = $state([])
  line = $state(null)
  units = $state([])
  neighbours = $state([])
  imageFailed = $state(false)
  skipped = $state([])

  // -- the selection -------------------------------------------------------------------------
  selection = $state([])
  anchor = $state(null)
  focused = $state(null)
  pointer = $state(null)
  mode = $state(null) // reading | note | candidates | draw-unit | draw-line | split
  candidates = $state(null)
  draft = $state('')
  help = $state(false)

  // -- what this client did ------------------------------------------------------------------
  events = $state([])
  undoStack = $state([])
  conflict = $state(null)
  toasts = $state([])
  warnings = $state([])

  openedAt = 0
  #undoRestored = false

  // -- derived -------------------------------------------------------------------------------

  /** The active unit records, in reading order. */
  ordered = $derived(orderUnits(this.units, this.line?.vertical ?? true))

  /** The selected units, in reading order; `focused` alone when nothing is selected. */
  targets = $derived.by(() => {
    const wanted = new Set(this.selection.length ? this.selection : this.focused ? [this.focused] : [])
    return this.ordered.filter((unit) => wanted.has(unit.id))
  })

  /** The unit the cursor is on: the last one of the selection, or the only target. */
  current = $derived.by(() => {
    if (this.focused) return this.ordered.find((unit) => unit.id === this.focused) ?? null
    return this.targets[this.targets.length - 1] ?? null
  })

  /** The box the line view shows: the line box grown a little, or the union of its units. */
  lineBox = $derived.by(() => {
    if (this.line?.box) return this.line.box
    return unionBox(this.units.map((unit) => unit.box))
  })

  /** The queue item after the open line, so that space can move on. */
  nextItem = $derived.by(() => {
    const index = this.queue.items.findIndex((item) => item.id === this.line?.id)
    return index >= 0 ? this.queue.items[index + 1] ?? null : this.queue.items[0] ?? null
  })

  /** Per-document progress, from the queue items and the document counts. */
  progress = $derived.by(() =>
    this.documents.map((document) => {
      const done = document.units ? document.reviewed / document.units : 0
      return { ...document, done: document.units ? Math.min(1, done) : document.reviewed ? 1 : 0 }
    }),
  )

  // -- the shell -----------------------------------------------------------------------------

  async start() {
    this.updateTheme()
    if (!this.#undoRestored) {
      this.#undoRestored = true
      try {
        this.undoStack = JSON.parse(stored(UNDO_KEY, '[]'))
      } catch {
        this.undoStack = []
      }
    }
    window.addEventListener('hashchange', () => this.#applyHash())
    try {
      await this.loadDocuments()
      this.status = 'ready'
      await this.#applyHash(true)
    } catch (error) {
      this.status = 'error'
      this.error = error.message
    }
  }

  async loadDocuments() {
    const body = await api.documents({ limit: 500 })
    this.documents = body.items
    return this.documents
  }

  async #applyHash(initial = false) {
    const hash = location.hash.replace(/^#\/?/, '')
    const [name, id] = hash.split('/')
    if (name === 'page' && id) {
      const pageId = decodeURIComponent(id)
      if (this.route.name === 'page' && this.page?.id === pageId) return
      if (this.page?.id === pageId && this.lines.length) {
        // The page is already loaded as the context of an open line: switch back to it without
        // fetching again, and leave the line so that its timing event is posted.
        await this.leaveLine()
        this.line = null
        this.units = []
        this.selection = []
        this.focused = null
        this.route = { name: 'page', id: pageId }
        return
      }
      await this.openPage(pageId)
      return
    }
    if (name === 'line' && id) {
      const lineId = decodeURIComponent(id)
      if (this.route.name === 'line' && this.line?.id === lineId) return
      await this.openLine(lineId)
      return
    }
    if (this.route.name !== 'queue' || initial) await this.openQueue()
  }

  go(name, id = null, { replace = false } = {}) {
    const hash = `#/${name}${id ? `/${encodeURIComponent(id)}` : ''}`
    if (location.hash === hash) return
    if (replace) history.replaceState(null, '', hash)
    else location.hash = hash
  }

  // -- the queue -----------------------------------------------------------------------------

  async openQueue() {
    await this.leaveLine()
    this.route = { name: 'queue', id: null }
    this.page = null
    this.line = null
    this.units = []
    this.queue.loading = true
    try {
      const [queue, documents] = await Promise.all([
        api.queue({
          strategy: this.strategy,
          document: this.documentFilter,
          limit: this.queue.limit,
          offset: this.queue.offset,
        }),
        api.documents({ limit: 500 }),
      ])
      this.queue = { ...queue, loading: false }
      this.documents = documents.items
    } catch (error) {
      this.queue = { ...this.queue, loading: false }
      this.fail(error)
    }
    this.go('queue')
  }

  async setStrategy(strategy) {
    this.strategy = strategy
    this.queue.offset = 0
    await this.openQueue()
  }

  async setDocumentFilter(documentId) {
    this.documentFilter = documentId
    this.queue.offset = 0
    await this.openQueue()
  }

  // -- a page --------------------------------------------------------------------------------

  async openPage(pageId) {
    await this.leaveLine()
    this.route = { name: 'page', id: pageId }
    this.imageFailed = false
    this.selection = []
    try {
      const [page, lines] = await Promise.all([
        api.page(pageId),
        api.pageLines(pageId, { limit: 2000 }),
      ])
      this.page = page
      this.lines = lines.items
      this.pageOpenedAt = Date.now()
    } catch (error) {
      this.fail(error)
      return
    }
    this.go('page', pageId)
  }

  async reloadPage() {
    if (!this.page) return
    const pageId = this.page.id
    const [page, lines] = await Promise.all([
      api.page(pageId),
      api.pageLines(pageId, { limit: 2000 }),
    ])
    this.page = page
    this.lines = lines.items
  }

  /** A page whose image is not in the cache and cannot be fetched is skipped, not opened. */
  async skipPage() {
    const index = this.queue.items.findIndex((item) => item.page_id === this.page?.id)
    this.skipped = [...this.skipped, this.page?.id].filter(Boolean)
    const next = this.queue.items.slice(index + 1).find((item) => !this.skipped.includes(item.page_id))
    if (next) await this.openLine(next.id)
    else await this.openQueue()
  }

  markImageFailed() {
    this.imageFailed = true
    this.toast('the page image is not available; the IIIF URL is shown instead', 'warn')
  }

  // -- a line --------------------------------------------------------------------------------

  async openLine(lineId, options = {}) {
    await this.leaveLine()
    this.route = { name: 'line', id: lineId }
    this.selection = []
    this.anchor = null
    this.focused = null
    this.imageFailed = false
    this.busy = true
    try {
      const units = await api.lineUnits(lineId, { limit: 2000 })
      this.units = units.items
      const line = await this.#lineById(lineId)
      this.line = line
      if (line) {
        if (options.syncPage !== false && (!this.page || this.page.id !== line.page_id)) {
          await this.#loadPageContext(line.page_id)
        }
        this.openedAt = Date.now()
        this.#pushUndoRestore()
        await this.#timing(line, 'open')
        this.#preselect(line, units.items)
      }
    } catch (error) {
      this.fail(error)
      this.busy = false
      return
    }
    this.busy = false
    this.go('line', lineId)
  }

  /** Whether the open line is the one asked for; used before an async answer overwrites state. */
  #stillOpen(lineId) {
    return this.route.name === 'line' && this.route.id === lineId
  }

  async #lineById(lineId) {
    // The line record itself is read from the page the queue named, or from the pages of the
    // dataset when the line was opened by URL.
    const cached = this.queue.items.find((item) => item.id === lineId)
    const pageId = cached?.page_id ?? lineId.split(':').slice(0, 2).join(':')
    const lines = await api.pageLines(pageId, { limit: 2000 })
    this.lines = lines.items
    return lines.items.find((line) => line.id === lineId) ?? null
  }

  async #loadPageContext(pageId) {
    const [page, lines] = await Promise.all([
      api.page(pageId),
      api.pageLines(pageId, { limit: 2000 }),
    ])
    this.page = page
    this.lines = lines.items
  }

  /** The neighbouring two lines, for context: `[previous, next]`, either null at the page's ends. */
  async loadNeighbours() {
    if (!this.line || !this.page) return
    const index = this.lines.findIndex((line) => line.id === this.line.id)
    const around = [this.lines[index - 1] ?? null, this.lines[index + 1] ?? null]
    try {
      const neighbours = await Promise.all(
        around.map(async (line) =>
          line ? { line, units: (await api.lineUnits(line.id, { limit: 2000 })).items } : null,
        ),
      )
      this.neighbours = neighbours
    } catch {
      this.neighbours = [null, null]
    }
  }

  async refreshLine() {
    if (!this.line) return
    const lineId = this.line.id
    const [units, lines] = await Promise.all([
      api.lineUnits(lineId, { limit: 2000 }),
      this.page ? api.pageLines(this.page.id, { limit: 2000 }) : Promise.resolve({ items: this.lines }),
    ])
    if (!this.#stillOpen(lineId)) return
    this.units = units.items
    this.lines = lines.items
    this.line = lines.items.find((line) => line.id === lineId) ?? this.line
    this.selection = this.selection.filter((id) => units.items.some((unit) => unit.id === id))
  }

  async leaveLine() {
    const line = this.line
    this.neighbours = []
    if (!line) return
    const dwell = this.openedAt ? Date.now() - this.openedAt : 0
    this.openedAt = 0
    await this.#timing(line, 'leave', dwell)
  }

  async nextLine() {
    const next = this.nextItem
    if (!next) {
      this.toast('the queue has no line after this one')
      return
    }
    await this.openLine(next.id)
  }

  async previousLine() {
    if (!this.line) return
    const index = this.lines.findIndex((line) => line.id === this.line.id)
    const previous = this.lines[index - 1]
    if (previous) await this.openLine(previous.id)
    else this.toast('this is the first line of the page')
  }

  // -- selection -----------------------------------------------------------------------------

  select(unitId, { extend = false, toggle = false } = {}) {
    this.focused = unitId
    if (unitId == null) {
      this.selection = []
      this.anchor = null
      return
    }
    if (extend && this.anchor) {
      const ids = this.ordered.map((unit) => unit.id)
      const from = ids.indexOf(this.anchor)
      const to = ids.indexOf(unitId)
      if (from >= 0 && to >= 0) {
        this.selection = ids.slice(Math.min(from, to), Math.max(from, to) + 1)
        return
      }
    }
    if (toggle && this.selection.includes(unitId)) {
      this.selection = this.selection.filter((id) => id !== unitId)
      return
    }
    this.anchor = unitId
    this.selection = [unitId]
  }

  /** Move the selection one unit along the line. */
  step(direction, { extend = false } = {}) {
    const ids = this.ordered.map((unit) => unit.id)
    if (!ids.length) return
    const current = this.focused ?? this.selection[this.selection.length - 1]
    const index = current ? ids.indexOf(current) : -1
    const next = Math.min(ids.length - 1, Math.max(0, index + direction))
    this.select(ids[next], { extend })
  }

  selectAll() {
    this.selection = this.ordered.map((unit) => unit.id)
  }

  // -- recording a decision ------------------------------------------------------------------

  /** Post one review. A 409 is kept for the conflict dialog and answered with null. */
  async post(request, { undoable = true, label = '', quiet = false } = {}) {
    const body = { client_id: this.clientId, idempotency_key: key(), ...request }
    try {
      const body_ = await api.reviews(body)
      const result = body_.results[0]
      this.#record(body, result, { undoable, label })
      if (result.warnings?.length) {
        this.warnings = [...this.warnings, ...result.warnings]
        for (const warning of result.warnings) this.toast(warning, 'warn')
      }
      if (!quiet && !result.duplicate) this.toast(`${label || result.field} recorded (${result.id})`, 'ok')
      return result
    } catch (error) {
      if (error instanceof ConflictError) {
        this.conflict = { request: body, detail: error.conflict, label }
        return null
      }
      this.fail(error)
      return null
    }
  }

  /** Post several reviews of one target in order, each from the revision the last one left. */
  async postChain(requests, options = {}) {
    let revision = requests[0]?.base_revision ?? null
    const results = []
    for (const request of requests) {
      const result = await this.post({ ...request, base_revision: revision }, options)
      if (!result) return results
      revision = result.revision
      results.push(result)
    }
    return results
  }

  async accept() {
    await this.#setReview('reviewed', 'accept')
  }

  async reject() {
    await this.#setReview('rejected', 'reject')
  }

  async #setReview(value, label) {
    const targets = this.targets
    if (!targets.length) {
      this.toast('select a unit first', 'warn')
      return
    }
    for (const unit of targets) {
      if (unit.review === value) continue
      await this.post(
        {
          target_type: 'unit',
          target_id: unit.id,
          field: 'review',
          new: value,
          base_revision: unit.revision,
        },
        { label: `${label} ${unit.id.split(':').pop()}` },
      )
    }
    await this.refreshLine()
  }

  /** `r`: set the reading of the selection; the last target takes the typed text. */
  beginReading(initial = null) {
    if (!this.current) {
      this.toast('select a unit first', 'warn')
      return
    }
    this.draft = initial ?? this.current.reading ?? this.current.text_source ?? ''
    this.mode = 'reading'
  }

  async setReading(text) {
    const unit = this.current
    this.mode = null
    if (!unit || text === null || text === undefined) return
    if (text === (unit.reading ?? '')) return
    await this.post(
      {
        target_type: 'unit',
        target_id: unit.id,
        field: 'reading',
        new: text,
        base_revision: unit.revision,
      },
      { label: `reading ${unit.id.split(':').pop()}` },
    )
    await this.refreshLine()
  }

  /** `j`: the code points the reading may have been written with. */
  async beginCandidates() {
    const unit = this.current
    if (!unit) {
      this.toast('select a unit first', 'warn')
      return
    }
    try {
      this.candidates = await api.candidates(unit.id)
      this.mode = 'candidates'
    } catch (error) {
      this.fail(error)
    }
  }

  /** Choosing a 字母 records the code point, the 字母 and 字母-shaped script of the candidate. */
  async chooseCandidate(candidate) {
    const unit = this.current
    this.mode = null
    this.candidates = null
    if (!unit || !candidate) return
    const requests = []
    if (candidate.unicode && candidate.unicode !== unit.unicode) {
      requests.push({
        target_type: 'unit',
        target_id: unit.id,
        field: 'unicode',
        new: candidate.unicode,
        base_revision: unit.revision,
      })
    }
    if (candidate.jibo && candidate.jibo !== unit.jibo) {
      requests.push({
        target_type: 'unit',
        target_id: unit.id,
        field: 'jibo',
        new: candidate.jibo,
        base_revision: unit.revision,
      })
    }
    const script = candidate.script ?? (candidate.jibo ? 'hentaigana' : null)
    if (script && script !== unit.script) {
      requests.push({
        target_type: 'unit',
        target_id: unit.id,
        field: 'script',
        new: script,
        base_revision: unit.revision,
      })
    }
    if (unit.classification !== 'identified') {
      requests.push({
        target_type: 'unit',
        target_id: unit.id,
        field: 'classification',
        new: 'identified',
        base_revision: unit.revision,
      })
    }
    if (!requests.length) {
      this.toast('the unit already carries that code point')
      return
    }
    await this.postChain(requests, { label: `字母 ${candidate.jibo ?? candidate.unicode}` })
    await this.refreshLine()
  }

  /** `g`: mark the selection an unresolved group (連綿), one `group_id` for all of it. */
  async markGroup() {
    const targets = this.targets
    if (targets.length < 2) {
      this.toast('select two or more units to group', 'warn')
      return
    }
    const lineId = this.line?.id ?? targets[0].line_id
    const groupId = `${lineId}:g${Math.random().toString(36).slice(2, 6)}`
    for (const unit of targets) {
      await this.postChain(
        [
          {
            target_type: 'unit',
            target_id: unit.id,
            field: 'group_id',
            new: groupId,
            base_revision: unit.revision,
          },
          {
            target_type: 'unit',
            target_id: unit.id,
            field: 'granularity',
            new: 'sequence',
            base_revision: unit.revision,
          },
        ],
        { label: `group ${groupId.split(':').pop()}` },
      )
    }
    await this.refreshLine()
  }

  /** `n`: a free note on the line, or on the focused unit. */
  beginNote() {
    this.draft = ''
    this.mode = 'note'
  }

  async addNote(text) {
    const target = this.current
    this.mode = null
    if (!text) return
    await this.post(
      {
        target_type: target ? 'unit' : 'line',
        target_id: target?.id ?? this.line.id,
        field: 'note',
        new: text,
      },
      { undoable: false, label: 'note' },
    )
  }

  /** `s`: cut the focused unit in two at the last pointer position (or its middle). */
  async splitAt(point = null) {
    const unit = this.current
    if (!unit?.box) {
      this.toast('select a unit with a box to split', 'warn')
      return
    }
    const vertical = this.line?.vertical ?? true
    const cut =
      point ??
      (vertical
        ? { x: unit.box.x + unit.box.w / 2, y: unit.box.y + unit.box.h / 2 }
        : { x: unit.box.x + unit.box.w / 2, y: unit.box.y + unit.box.h / 2 })
    const halves = splitBox(unit.box, cut, vertical)
    if (!halves) {
      this.toast('the cut would leave less than four pixels; point further inside', 'warn')
      return
    }
    await this.post(
      {
        target_type: 'unit',
        target_id: unit.id,
        field: 'segmentation',
        new: {
          split: halves.map((box) => ({
            box,
            reading: unit.reading ?? null,
            text_source: unit.text_source ?? null,
            unicode: unit.unicode ?? null,
            jibo: unit.jibo ?? null,
            script: unit.script,
            kind: unit.kind,
          })),
        },
        base_revision: unit.revision,
      },
      { label: `split ${unit.id.split(':').pop()}` },
    )
    this.selection = []
    await this.refreshLine()
  }

  /** `m`: merge the selection into one unit. */
  async mergeSelection() {
    const targets = this.targets
    if (targets.length < 2) {
      this.toast('select two or more units to merge', 'warn')
      return
    }
    const last = targets[targets.length - 1]
    await this.post(
      {
        target_type: 'unit',
        target_id: last.id,
        field: 'segmentation',
        new: { merge: targets.map((unit) => unit.id) },
        base_revision: last.revision,
      },
      { label: `merge ${targets.length} units` },
    )
    this.selection = []
    await this.refreshLine()
  }

  /** `c`: draw a unit for a character the detector missed. */
  beginDrawUnit() {
    if (!this.line) return
    this.mode = 'draw-unit'
    this.draft = ''
  }

  /** `c` then draw: the drawn box becomes a unit of the line. */
  async createUnit(box, extra = {}) {
    this.mode = null
    if (!this.line) return
    try {
      const result = await api.createUnit({
        line_id: this.line.id,
        box,
        reading: extra.reading || null,
        text_source: extra.reading || null,
        kind: extra.kind ?? 'char',
        client_id: this.clientId,
        idempotency_key: key(),
      })
      this.#record(
        { target_type: 'unit', target_id: result.target_id, field: 'create', new: result.state },
        result,
        { undoable: true, label: 'new unit' },
      )
      this.toast(`unit ${result.target_id.split(':').pop()} created`, 'ok')
      await this.refreshLine()
      this.select(result.target_id)
    } catch (error) {
      this.fail(error)
    }
  }

  beginDrawLine() {
    if (!this.page) return
    this.pageOpenedAt ??= Date.now()
    this.mode = 'draw-line'
  }

  /** Draw a line the detector missed. */
  async createLine(box, extra = {}) {
    this.mode = null
    if (!this.page) return
    try {
      const result = await api.createLine({
        page_id: this.page.id,
        box,
        text_raw: extra.text_raw ?? '',
        text: extra.text ?? '',
        client_id: this.clientId,
        idempotency_key: key(),
      })
      this.#record(
        { target_type: 'line', target_id: result.target_id, field: 'create', new: result.state },
        result,
        { undoable: true, label: 'new line' },
      )
      this.toast(`line ${result.target_id} created`, 'ok')
      await this.reloadPage()
      await this.openLine(result.target_id)
    } catch (error) {
      this.fail(error)
    }
  }

  // -- the conflict dialog -------------------------------------------------------------------

  /** Apply the refused change again, from the revision the server reported. */
  async reapplyConflict() {
    const pending = this.conflict
    if (!pending) return
    this.conflict = null
    const request = { ...pending.request, base_revision: pending.detail?.revision ?? null }
    await this.post(request, { label: `${pending.label} (reapplied)` })
    await this.refreshLine()
  }

  /** Drop the refused change and take the server's state as it is. */
  async discardConflict() {
    const pending = this.conflict
    this.conflict = null
    if (!pending) return
    if (pending.request.target_type === 'unit' && this.line) await this.refreshLine()
    if (pending.request.target_type === 'line' && this.page) await this.reloadPage()
    this.toast('the change was discarded; the server state is shown')
  }

  // -- undo ----------------------------------------------------------------------------------

  /** `z`: the last event of this client is undone by a compensating review. */
  async undo() {
    const [entry, ...rest] = this.undoStack
    if (!entry) {
      this.toast('this client has nothing to undo')
      return
    }
    if (entry.field === 'timing') {
      this.undoStack = rest
      this.#persistUndo()
      await this.undo()
      return
    }
    const compensations = []
    let note = ''
    if (entry.field === 'create') {
      const created = entry.created ?? [entry.target_id]
      for (const id of created) {
        compensations.push({
          target_type: entry.target_type,
          target_id: id,
          field: 'active',
          new: false,
          evidence: `undo of ${entry.event_id}`,
        })
        compensations.push({
          target_type: entry.target_type,
          target_id: id,
          field: 'review',
          new: 'rejected',
          evidence: `undo of ${entry.event_id}`,
        })
      }
    } else if (entry.field === 'segmentation') {
      // The outputs are retired, which is a review of an active unit and is accepted. The inputs
      // stay retired: the store refuses any review of a retired unit, including one that would set
      // `active` back to true, so a split is not reversible through reviews. Drawing the inputs
      // again with `c` is what the reviewer does; `atlas review replay` is what repairs a log.
      for (const id of entry.created ?? []) {
        compensations.push({
          target_type: 'unit',
          target_id: id,
          field: 'active',
          new: false,
          evidence: `undo of ${entry.event_id}`,
        })
      }
      if (entry.retired?.length) {
        note = `${entry.retired.length} input unit(s) stay retired: the service refuses a review of a retired unit — draw them again with c`
      }
    } else {
      compensations.push({
        target_type: entry.target_type,
        target_id: entry.target_id,
        field: entry.field,
        new: entry.old,
        evidence: `undo of ${entry.event_id}`,
      })
    }
    this.undoStack = rest
    this.#persistUndo()
    for (const request of compensations) {
      await this.post(request, { undoable: false, label: `undo of ${entry.event_id}`, quiet: true })
    }
    this.toast(`undone: ${entry.label || entry.field}`, 'ok')
    if (note) this.toast(note, 'warn')
    await this.refreshLine()
  }

  #record(request, result, { undoable = true, label = '' } = {}) {
    const event = {
      id: result.id,
      at: new Date().toISOString(),
      target_type: result.target_type,
      target_id: result.target_id,
      field: result.field,
      old: result.review?.old ?? null,
      new: result.review?.new ?? request.new ?? null,
      revision: result.revision,
      duplicate: result.duplicate,
      created: result.created ?? [],
      retired: result.retired ?? [],
      warnings: result.warnings ?? [],
      label,
    }
    this.events = [event, ...this.events].slice(0, 200)
    if (undoable && !result.duplicate) {
      this.undoStack = [
        {
          event_id: result.id,
          target_type: result.target_type,
          target_id: result.target_id,
          field: result.field,
          old: result.review?.old ?? null,
          new: result.review?.new ?? null,
          created: result.created ?? [],
          retired: result.retired ?? [],
          label,
        },
        ...this.undoStack,
      ].slice(0, 100)
      this.#persistUndo()
    }
  }

  #persistUndo() {
    remember(UNDO_KEY, JSON.stringify(this.undoStack))
  }

  async #pushUndoRestore() {
    /* the stack is restored from storage once, at start */
  }

  /** Start on the first unit no event has touched, so a half-reviewed line continues where it stopped. */
  #preselect(line, units) {
    const ordered = orderUnits(units, line.vertical)
    const first = ordered.find((unit) => !unit.revision) ?? ordered[0]
    if (first) this.select(first.id)
  }

  // -- timing --------------------------------------------------------------------------------

  /** A `timing` event: how long a line was open, posted on open and on leave. */
  async #timing(line, action, dwell = 0) {
    const now = Date.now()
    const payload =
      action === 'open'
        ? { action, opened_ms: now }
        : { action, closed_ms: now, dwell_ms: dwell, units: this.units.length }
    try {
      await this.post(
        { target_type: 'line', target_id: line.id, field: 'timing', new: payload },
        { undoable: false, quiet: true, label: 'timing' },
      )
    } catch {
      /* a timing event never blocks the review */
    }
  }

  // -- reporting -----------------------------------------------------------------------------

  fail(error) {
    const message = error instanceof ApiError ? `${error.path}: ${error.message}` : String(error?.message ?? error)
    this.error = message
    this.toast(message, 'error')
    if (!(error instanceof ApiError)) console.error(error)
  }

  toast(message, kind = 'info') {
    const id = Math.random().toString(36).slice(2)
    this.toasts = [...this.toasts, { id, message, kind }]
    setTimeout(() => {
      this.toasts = this.toasts.filter((toast) => toast.id !== id)
    }, kind === 'error' ? 9000 : 4000)
  }

  dismissToast(id) {
    this.toasts = this.toasts.filter((toast) => toast.id !== id)
  }

  clearWarnings() {
    this.warnings = []
  }

  // -- the shell's own state -----------------------------------------------------------------

  setClientId(value) {
    const clean = value.trim()
    if (!clean) return
    this.clientId = clean
    remember(CLIENT_KEY, clean)
  }

  toggleTheme() {
    this.theme = this.theme === 'dark' ? 'light' : this.theme === 'light' ? 'system' : 'dark'
    remember(THEME_KEY, this.theme)
    this.updateTheme()
  }

  updateTheme() {
    const dark =
      this.theme === 'dark' ||
      (this.theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.dataset.theme = this.theme === 'system' ? '' : this.theme
    document.documentElement.dataset.resolvedTheme = dark ? 'dark' : 'light'
  }

  cancel() {
    if (this.mode) {
      this.mode = null
      this.candidates = null
      return
    }
    if (this.help) {
      this.help = false
      return
    }
    this.selection = []
    this.focused = null
  }

  setPointer(point) {
    this.pointer = point
  }

  /** The keys of the line view and the page view, as the help overlay lists them. */
  static bindings = [
    { keys: 'a', what: 'accept the selection (review = reviewed)' },
    { keys: 'x', what: 'reject the selection (review = rejected)' },
    { keys: 'drag', what: 'move a unit; drag a corner to resize' },
    { keys: 's', what: 'split the unit at the pointer' },
    { keys: 'm', what: 'merge the selection into one unit' },
    { keys: 'c', what: 'draw a unit for a character with no box' },
    { keys: 'l', what: 'page view: draw a line the detector missed' },
    { keys: 'r', what: 'set the reading of the focused unit' },
    { keys: 'j', what: 'choose the 字母 of the focused unit' },
    { keys: 'g', what: 'mark the selection an unresolved group' },
    { keys: 'n', what: 'note on the focused unit or the line' },
    { keys: 'z', what: 'undo this client’s last event (a compensating review)' },
    { keys: 'space', what: 'the next line of the queue' },
    { keys: '←↑→↓', what: 'move the selection; shift extends it' },
    { keys: 'enter', what: 'open the focused line' },
    { keys: 'escape', what: 'cancel the mode, then clear the selection' },
    { keys: '?', what: 'this list' },
  ]
}

export default Session
