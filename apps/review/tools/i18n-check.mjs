#!/usr/bin/env bun
/**
 * Cross-checks the interface strings against the locale catalogues.
 *
 * Static analysis only: it reads every `.js`/`.svelte` file under `src`, finds each `t(...)` call
 * and pulls out the literal keys inside it — including both branches of a ternary such as
 * `t(ok ? 'a.b' : 'a.c')`. A template-literal key such as `` t(`script.${key}`) `` cannot be resolved
 * statically; those are turned into a wildcard pattern (`script.*`) instead, and any catalogue key the
 * pattern matches counts as used.
 *
 * Fails the run if:
 *   - a literal key the source calls is missing from en.json (a plural key counts as present when
 *     either the bare key or both `.one` and `.other` exist);
 *   - en.json holds a key that no source file's literal or wildcard use reaches;
 *   - a non-English locale holds a key that is not in en.json.
 * Reports, per non-English locale, how many of en.json's keys it does not yet have — informational,
 * not a failure, since translation happens on its own schedule.
 *
 * Run with: bun tools/i18n-check.mjs
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, extname } from 'node:path'

const ROOT = join(import.meta.dir, '..')
const SRC = join(ROOT, 'src')
const LOCALES_DIR = join(ROOT, 'src/locales')
const PLURAL_SUFFIXES = ['zero', 'one', 'two', 'few', 'many', 'other']

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    const info = statSync(path)
    if (info.isDirectory()) walk(path, out)
    else if (['.js', '.svelte'].includes(extname(entry))) out.push(path)
  }
  return out
}

/** Every balanced `t(...)` call's key expression — up to the top-level comma before `params`. */
function tCalls(text) {
  const calls = []
  const re = /\bt\(/g
  let match
  while ((match = re.exec(text))) {
    let depth = 1, i = match.index + match[0].length
    const start = i
    let keyEnd = -1
    while (i < text.length && depth > 0) {
      const ch = text[i]
      if (ch === '(' || ch === '{' || ch === '[') depth++
      else if (ch === ')' || ch === '}' || ch === ']') depth--
      else if (ch === ',' && depth === 1 && keyEnd < 0) keyEnd = i
      else if (ch === "'" || ch === '"' || ch === '`') {
        // Skip over a whole string so a comma or bracket inside it is never mistaken for structure.
        i++
        while (i < text.length && text[i] !== ch) { if (text[i] === '\\') i++; i++ }
      }
      i++
    }
    calls.push(text.slice(start, keyEnd >= 0 ? keyEnd : i - 1))
  }
  return calls
}

const KEY = '[a-zA-Z0-9_.]+'
const literalRe = new RegExp(`['"\`](${KEY})['"\`]`, 'g')
// A template literal key with one or more `${...}` holes, e.g. `script.${key}` or `issue.${id}.title`.
const templateRe = /`([a-zA-Z0-9_.]*(?:\$\{[^}]*\}[a-zA-Z0-9_.]*)+)`/g

const usedLiteral = new Set()
const usedPatterns = []

for (const file of walk(SRC)) {
  const text = readFileSync(file, 'utf8')
  for (const call of tCalls(text)) {
    // Literal keys: every quoted, non-templated token that looks like a key. This also picks up
    // both sides of a ternary passed as the first argument.
    for (const m of call.matchAll(literalRe)) usedLiteral.add(m[1])
    for (const m of call.matchAll(templateRe)) {
      const pattern = '^' + m[1].replace(/\$\{[^}]*\}/g, '[a-zA-Z0-9_]+').replace(/\./g, '\\.') + '$'
      usedPatterns.push(new RegExp(pattern))
    }
  }
}

function isUsed(key) {
  if (usedLiteral.has(key)) return true
  const dot = key.lastIndexOf('.')
  const suffix = dot >= 0 ? key.slice(dot + 1) : ''
  const base = PLURAL_SUFFIXES.includes(suffix) ? key.slice(0, dot) : null
  if (base && usedLiteral.has(base)) return true
  return usedPatterns.some(p => p.test(key) || (base && p.test(base)))
}

const en = JSON.parse(readFileSync(join(LOCALES_DIR, 'en.json'), 'utf8'))
const enKeys = Object.keys(en)
const enKeySet = new Set(enKeys)

let failed = false

// 1. Every literal key the source calls must resolve in en.json (bare, or a complete plural pair).
const missing = []
for (const key of usedLiteral) {
  if (enKeySet.has(key)) continue
  if (enKeySet.has(`${key}.one`) && enKeySet.has(`${key}.other`)) continue
  missing.push(key)
}
if (missing.length) {
  failed = true
  console.error(`Missing from en.json (${missing.length}):`)
  for (const key of missing.sort()) console.error(`  ${key}`)
}

// 2. Every en.json key must be reachable from some literal or wildcard use in the source.
const unused = enKeys.filter(key => !isUsed(key))
if (unused.length) {
  failed = true
  console.error(`In en.json but unused by any src/ call (${unused.length}):`)
  for (const key of unused.sort()) console.error(`  ${key}`)
}

// 3. A translated locale may lag behind en.json (informational), but never invent its own keys.
for (const file of readdirSync(LOCALES_DIR)) {
  if (file === 'en.json') continue
  const tag = file.replace(/\.json$/, '')
  const messages = JSON.parse(readFileSync(join(LOCALES_DIR, file), 'utf8'))
  const localeKeys = Object.keys(messages)
  const foreign = localeKeys.filter(key => !enKeySet.has(key))
  if (foreign.length) {
    failed = true
    console.error(`${tag}.json has keys not in en.json (${foreign.length}):`)
    for (const key of foreign.sort()) console.error(`  ${key}`)
  }
  const lacking = enKeys.filter(key => !(key in messages))
  console.log(`${tag}: lacks ${lacking.length} of ${enKeys.length} en.json keys`)
}

if (failed) { console.error('i18n-check failed.'); process.exit(1) }
console.log(`i18n-check passed. en.json has ${enKeys.length} keys.`)
