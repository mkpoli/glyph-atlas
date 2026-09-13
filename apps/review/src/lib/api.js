/**
 * The client of the T40 review service.
 *
 * Every call returns the parsed body and throws `ApiError` on any non-2xx answer. A 409 carries the
 * server's `detail` object (`error`, `revision`, `base_revision`, `state`) and is thrown as
 * `ConflictError`, so the interface can offer to reapply or discard the change.
 */

/** The base URL of the review service: same origin when the built app is served by it. */
export const base = ''

export class ApiError extends Error {
  constructor(status, detail, path) {
    super(typeof detail === 'string' ? detail : `${path}: HTTP ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.path = path
  }
}

export class ConflictError extends ApiError {
  constructor(detail, path) {
    super(409, detail, path)
    this.name = 'ConflictError'
    /** `{error, target_type, target_id, base_revision, revision, state}`. */
    this.conflict = detail ?? {}
  }
}

async function request(path, { method = 'GET', body, signal, clientId } = {}) {
  const response = await fetch(`${base}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
      ...(clientId ? { 'x-atlas-client': clientId } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : payload
    if (response.status === 409) throw new ConflictError(detail, path)
    throw new ApiError(response.status, detail, path)
  }
  return payload
}

function query(params) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const api = {
  project: () => request('/project'),
  pages: (params = {}) => request(`/pages${query(params)}`),
  corrections: (pageId) => request(`/pages/${encodeURIComponent(pageId)}/corrections`),
  saveCorrection: (body, clientId) => request('/corrections', { method: 'POST', body, clientId }),
  retractCorrection: (id, body, clientId) => request(`/corrections/${encodeURIComponent(id)}/retract`, {
    method: 'POST', body, clientId,
  }),
  sourceUpdates: (pageIds) => request('/source-updates', { method: 'POST', body: { page_ids: pageIds } }),
  /** `GET /documents` — every document with its page, line, unit and reviewed counts. */
  documents: (params = {}) => request(`/documents${query(params)}`),

  /** `GET /pages/{id}` — one page, its counts and the URL its image is served from. */
  page: (pageId, params = {}) => request(`/pages/${encodeURIComponent(pageId)}${query(params)}`),

  /** `GET /pages/{id}/lines` — the lines of a page in reading order. */
  pageLines: (pageId, params = {}) =>
    request(`/pages/${encodeURIComponent(pageId)}/lines${query(params)}`),

  /** `GET /lines/{id}/units` — the active units of a line, each with its revision. */
  lineUnits: (lineId, params = {}) =>
    request(`/lines/${encodeURIComponent(lineId)}/units${query(params)}`),

  /** `GET /units/{id}/candidates` — code points, 字母, reference glyphs and scores. */
  candidates: (unitId) => request(`/units/${encodeURIComponent(unitId)}/candidates`),

  /** `GET /queue` — the lines to review, by strategy, document and page. */
  queue: (params = {}) => request(`/queue${query(params)}`),

  /** `POST /reviews` — one decision, or a list of them; answers `{results}`. */
  reviews: (reviews) => request('/reviews', { method: 'POST', body: reviews }),

  /** `POST /lines` — a line the detector missed. */
  createLine: (line) => request('/lines', { method: 'POST', body: line }),

  /** `POST /units` — a unit drawn on an existing line. */
  createUnit: (unit) => request('/units', { method: 'POST', body: unit }),
}

export default api
