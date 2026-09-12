#!/usr/bin/env bun
/**
 * Shared scaffolding for the two tools that need a running service: a fixture dataset, the review
 * server over it, and a way to read back the events it recorded.
 *
 * `tools/check.mjs`, `tools/browser-check.mjs` and `tools/shots.mjs` all boot through here, so they
 * run against the same dataset and the same commands.
 */

import { Database } from 'bun:sqlite'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

export const HERE = import.meta.dir
export const ROOT = resolve(HERE, '..', '..', '..')
export const DIST = join(ROOT, 'apps', 'review', 'dist')

/** The command-line options the three tools share. */
export function options(argv = process.argv.slice(2)) {
  const value = (name, fallback = null) => {
    const index = argv.indexOf(`--${name}`)
    if (index >= 0 && argv[index + 1] && !argv[index + 1].startsWith('--')) return argv[index + 1]
    return fallback
  }
  return {
    keep: argv.includes('--keep'),
    python: value('python', join(ROOT, '.venv', 'bin', 'python')),
    port: Number(value('port', String(8800 + Math.floor(Math.random() * 400)))),
    directory: resolve(value('directory', mkdtempSync(join(tmpdir(), 'atlas-review-')))),
    external: value('server', null),
    headless: value('chrome', null),
  }
}

/** The Chromium of the Playwright cache, newest first; `--chrome` overrides it. */
export function chromePath(explicit = null) {
  if (explicit) return explicit
  const candidates = []
  const cache = join(process.env.HOME ?? '/root', '.cache', 'ms-playwright')
  if (existsSync(cache)) {
    for (const entry of new Bun.Glob('chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell').scanSync(cache)) {
      candidates.push(join(cache, entry))
    }
    for (const entry of new Bun.Glob('chromium-*/chrome-linux64/chrome').scanSync(cache)) {
      candidates.push(join(cache, entry))
    }
  }
  for (const path of ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome']) {
    if (existsSync(path)) candidates.push(path)
  }
  const sorted = candidates
    .filter((path) => existsSync(path))
    .sort((a, b) => {
      const version = (path) => Number(/(\d{3,})/.exec(path)?.[1] ?? 0) + (path.includes('headless_shell') ? 100000 : 0)
      return version(b) - version(a)
    })
  return sorted[0] ?? null
}

/** Build the fixture dataset and start the service over it. */
export async function boot(config) {
  const base = config.external ? config.external.replace(/\/$/, '') : `http://127.0.0.1:${config.port}`
  let fixture = null
  let server = null
  const log = []

  if (!config.external) {
    // A dataset directory that a previous run left behind would carry its reviewed state in
    // `review.sqlite`, and the queue would answer with what that run already reviewed.
    for (const stale of ['review.sqlite', 'review.sqlite-wal', 'review.sqlite-shm', 'reviews.jsonl']) {
      rmSync(join(config.directory, stale), { force: true })
    }
    const build = Bun.spawn([config.python, join(HERE, 'fixture.py'), config.directory], {
      stdout: 'pipe',
      stderr: 'pipe',
    })
    const [out, err, code] = await Promise.all([
      new Response(build.stdout).text(),
      new Response(build.stderr).text(),
      build.exited,
    ])
    if (code !== 0) throw new Error(`fixture.py failed (${code}): ${err.trim()}`)
    fixture = JSON.parse(out.trim().split('\n').pop())

    server = Bun.spawn(
      [config.python, '-m', 'glyph_atlas.cli', 'review', 'serve', config.directory, '--port', String(config.port)],
      {
        cwd: ROOT,
        env: {
          ...process.env,
          GLYPH_ATLAS_CACHE: join(config.directory, 'cache'),
          PYTHONPATH: join(ROOT, 'src'),
          ATLAS_OCR_MODEL_DIR: join(config.directory, 'models'),
          ATLAS_CLASSIFIER_MODEL: join(config.directory, 'no-classifier.onnx'),
        },
        stdout: 'pipe',
        stderr: 'pipe',
      },
    )
    for (const stream of [server.stdout, server.stderr]) {
      ;(async () => {
        for await (const chunk of stream) log.push(new TextDecoder().decode(chunk))
      })()
    }
    const deadline = Date.now() + 45000
    let up = false
    while (Date.now() < deadline && !up) {
      if (server.exitCode !== null) throw new Error(`the service exited with ${server.exitCode}:\n${log.join('')}`)
      try {
        up = (await fetch(`${base}/documents`)).status === 200
      } catch {
        /* not up yet */
      }
      if (!up) await Bun.sleep(200)
    }
    if (!up) throw new Error(`the service did not answer on ${base} within 45 s:\n${log.join('')}`)
  }

  return {
    base,
    fixture,
    log,
    stop({ keep = false } = {}) {
      server?.kill()
      if (!keep && !config.external) rmSync(config.directory, { recursive: true, force: true })
    },
  }
}

/** Read-only connections wait for the service's writes instead of failing with `database is locked`. */
function reader(directory) {
  const database = new Database(join(directory, 'review.sqlite'), { readonly: true })
  database.exec('PRAGMA busy_timeout = 5000')
  return database
}

/** The events the service recorded, oldest first, with the JSON columns parsed. */
export function events(directory) {
  const database = reader(directory)
  try {
    return database
      .query(
        'SELECT id, target_type, target_id, field, old, new, role, actor, evidence, client_id, ' +
          'idempotency_key, result FROM events ORDER BY seq',
      )
      .all()
      .map((row) => ({
        ...row,
        old: row.old ? JSON.parse(row.old) : null,
        new: row.new ? JSON.parse(row.new) : null,
      }))
  } finally {
    database.close()
  }
}

/** The unit rows of the store, active or not, keyed by id. */
export function units(directory) {
  const database = reader(directory)
  try {
    return Object.fromEntries(
      database
        .query('SELECT id, active, data FROM units')
        .all()
        .map((row) => [row.id, { ...JSON.parse(row.data), active: Boolean(row.active) }]),
    )
  } finally {
    database.close()
  }
}
