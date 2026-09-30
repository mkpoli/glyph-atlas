#!/usr/bin/env bun
// What the explorer's browse grid shows first: the collection's own crops, then the corpus sample.
//
// Run through devrun:
//   devrun bun apps/review/tools/home-order-check.mjs
import { rmSync } from 'node:fs'
import { join } from 'node:path'
import { boot, options, HERE } from './harness.mjs'

// The corpus fixture writes beside the collection and the run removes the whole tree afterwards, so
// the tree has to be one this run made: a supplied `--directory` would be written over and removed.
// Refused before `options()`, which makes its own directory for every run that gets that far.
if (process.argv.includes('--directory')) throw new Error('This check builds and removes its own fixture tree; do not point --directory at it.')
const config = options()
if (config.external) throw new Error('This check reads a page; use its disposable fixture.')
const root = config.directory
config.directory = join(root, 'collection')
const assert = (ok, message) => { if (!ok) throw new Error(message) }
let service = null
try {
  const seed = Bun.spawn([config.python, join(HERE, 'corpus-fixture.py'), config.directory], { stdout: 'pipe', stderr: 'pipe' })
  const [, err, code] = await Promise.all([new Response(seed.stdout).text(), new Response(seed.stderr).text(), seed.exited])
  if (code) throw new Error(err)
  service = await boot(config)
  const html = await (await fetch(service.base + '/en')).text()
  const order = [...html.matchAll(/data-(corpus|unit)="/g)].map(m => m[1] === 'corpus' ? 'corpus' : 'local')
  console.log(order.map((kind, i) => `${i} ${kind}`).join('\n'))
  assert(order.includes('local'), 'the grid drew no collection crop')
  assert(order.includes('corpus'), 'the grid drew no corpus crop')
  const firstCorpus = order.indexOf('corpus')
  assert(order.slice(0, firstCorpus).every(kind => kind === 'local') && order.slice(firstCorpus).every(kind => kind === 'corpus'),
    `a corpus crop sits among the collection's: ${order.join(',')}`)
  console.log('PASS the collection leads the browse grid, the corpus sample follows')
} finally {
  await service?.stop()
  if (!config.keep) rmSync(root, { recursive: true, force: true })
}
