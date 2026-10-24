// Where a page whose book has a IIIF manifest opens in a viewer outside the atlas, at that page when
// the record places it in the manifest: the CODH IIIF Curation Viewer by the canvas id, the Universal
// Viewer by the canvas's 0-based position.
const CURATION = 'https://codh.rois.ac.jp/software/iiif-curation-viewer/demo/'
const UNIVERSAL = 'https://www.universalviewer.dev/uv.html'

const web = url => typeof url === 'string' && /^https?:\/\//i.test(url)

/** The viewer links of a page record, or null when its book has no manifest. */
export function viewerLinks(page) {
  if (!web(page?.manifest)) return null
  const placed = Number.isInteger(page.canvas_index) && web(page.canvas)
  const curation = new URLSearchParams({ manifest: page.manifest, ...(placed ? { canvas: page.canvas } : {}) })
  const universal = new URLSearchParams({ manifest: page.manifest, ...(placed ? { cv: String(page.canvas_index) } : {}) })
  return { manifest: page.manifest, curation: `${CURATION}?${curation}`, universal: `${UNIVERSAL}#?${universal}` }
}
