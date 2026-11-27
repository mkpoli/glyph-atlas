// Sheets for choosing what the site's composer draws a part no code point names with. Each part is a
// block; each row is a character holding the part, its glyph shaded, then the character composed with
// each candidate drawing of the part (scripts/part_donor_candidates.py). The choices are kept in
// data/vocab/custom-part-donors.tsv.
//
//   bun tools/part-donor-sheets.mjs candidates.json out/ [from] [to] [per sheet] [parts: many|one|any] [characters]
//
// `many` (the default) draws the parts with a choice to make, two characters each; checking a choice takes
// the chosen drawing alone (`one`) in three.
import { compose, drawingSvg, fromUrl } from 'han-compose'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import Browser from './browser.mjs'
import { COMPOSER_VERSION } from '../../cloudflare/src/composerVersions.ts'
const [input, dir, from = '0', to = '100000', per = '12', which = 'many', count = '2'] = process.argv.slice(2)
mkdirSync(dir, { recursive: true })
const all = JSON.parse(readFileSync(input, 'utf8')).filter(row => which === 'any' || (which === 'one' ? row.candidates.length === 1 : row.candidates.length > 1))
const rows = all.slice(Number(from), Number(to))
const source = fromUrl(`https://ids-input-composer.mkpoli.workers.dev/${COMPOSER_VERSION}/`)
const svg = async text => {
  for (let i = 0; i < 4; i++) {
    try {
      // A drawing the composer makes only approximately (an operator it does not draw) is no candidate.
      const drawn = await compose(source, text)
      return drawn.approximate?.length ? `<span style="color:#b00;font-size:10px">approximate: ${esc(drawn.approximate.join(' '))}</span>` : drawingSvg(drawn, { size: 72, padding: 2 })
    }
    catch (e) { const m = String(e?.message ?? e); if (/No glyph|more components|fewer components|not a/i.test(m)) return null; await new Promise(r => setTimeout(r, 1500 * (i + 1))) }
  }
  return null
}
const donorOf = c => c.startsWith('㇯') ? [...c.replace(/^㇯+/, '')][0] : null
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
const cell = (inner, caption, ref) => `<td style="padding:2px 4px;text-align:center;vertical-align:top;${ref ? 'background:#eef4ff' : ''}"><div style="width:72px;height:72px">${inner ?? '<span style="color:#b00;font-size:10px">cannot draw</span>'}</div><div style="font-size:12px;font-weight:700">${esc(caption)}</div></td>`
const b = await Browser.launch({ width: 900, height: 800 })
try {
  for (let start = 0; start < rows.length; start += Number(per)) {
    const batch = rows.slice(start, start + Number(per)), index = []
    let html = '<!doctype html><meta charset=utf-8><body style="margin:0;background:#fff;font:12px sans-serif">'
    for (const row of batch) {
      const hosts = row.hosts.filter(h => row.candidates.some(c => donorOf(c) !== h.char)).slice(0, Number(count))
      if (!hosts.length) continue
      index.push({ key: row.key, candidates: row.candidates, hosts: hosts.map(h => h.char) })
      html += `<table style="border-collapse:collapse;border-top:2px solid #333;margin-bottom:2px"><tr><td style="width:90px;font-weight:700;font-size:14px">${row.key}</td><td></td>${row.candidates.map((_, i) => `<td style="text-align:center;font-weight:700;font-size:14px">${i + 1}</td>`).join('')}</tr>`
      for (const host of hosts) {
        html += `<tr><td style="font-size:11px">${esc(host.char)}</td>${cell(await svg(host.char), 'glyph', true)}`
        for (const [i, c] of row.candidates.entries())
          html += donorOf(c) === host.char ? cell('<span style="color:#999;font-size:10px">own donor</span>', i + 1) : cell(await svg(host.text.split(host.name).join(c)), i + 1)
        html += '</tr>'
      }
      html += '</table>'
    }
    if (!index.length) continue
    const name = `${dir}/sheet-${String(Number(from) + start).padStart(4, '0')}`
    writeFileSync(name + '.html', html); writeFileSync(name + '.json', JSON.stringify(index))
    await b.send('Page.navigate', { url: pathToFileURL(resolve(name + '.html')).href }); await new Promise(r => setTimeout(r, 1200))
    const { width, height } = JSON.parse(await b.evaluate('JSON.stringify({ width: document.body.scrollWidth, height: document.body.scrollHeight })'))
    await b.send('Emulation.setDeviceMetricsOverride', { width: Math.max(width, 300), height, deviceScaleFactor: 1, mobile: false })
    const s = await b.send('Page.captureScreenshot', { format: 'png', clip: { x: 0, y: 0, width: Math.max(width, 300), height, scale: 1 } })
    writeFileSync(name + '.png', Buffer.from(s.data, 'base64'))
    console.log(name, index.length)
  }
} finally { await b.close() }
