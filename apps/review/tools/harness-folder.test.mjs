import { afterAll, expect, test } from 'bun:test'
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { sweep } from './harness.mjs'

const parent = mkdtempSync(join(tmpdir(), 'harness-folder-test-'))
afterAll(() => rmSync(parent, { recursive: true, force: true }))
const held = () => readdirSync(parent).sort()

/** A run that takes its folder as a check does, says where it is, and waits. */
async function run(argv = []) {
  const child = Bun.spawn([process.execPath, '-e', `const { options } = await import(${JSON.stringify(join(import.meta.dir, 'harness.mjs'))})
    console.log(options(${JSON.stringify(argv)}).directory); await Bun.sleep(60000)`], { env: { ...process.env, TMPDIR: parent }, stdout: 'pipe', stderr: 'inherit' })
  const reader = child.stdout.getReader(), { value } = await reader.read()
  return { child, directory: new TextDecoder().decode(value).trim() }
}

test('a run told to stop takes its folder with it', async () => {
  for (const [signal, code] of [['SIGTERM', 143], ['SIGINT', 130], ['SIGHUP', 129]]) {
    const { child, directory } = await run()
    expect(existsSync(directory)).toBe(true)
    child.kill(signal)
    expect([await child.exited, existsSync(directory)]).toEqual([code, false])
  }
})

test('a folder of a run killed outright goes with the next run, and one of a run still going stays', async () => {
  const killed = await run(), going = await run()
  killed.child.kill('SIGKILL')
  await killed.child.exited
  expect(existsSync(killed.directory)).toBe(true)
  const next = await run()
  expect([existsSync(killed.directory), existsSync(going.directory), existsSync(next.directory)]).toEqual([false, true, true])
  for (const { child } of [going, next]) { child.kill(); await child.exited }
  expect(held()).toEqual([])
})

test('a folder kept on request, one given by hand and one nobody signed stay', async () => {
  const kept = await run(['--keep']), given = join(parent, 'given')
  kept.child.kill(); await kept.child.exited
  const byHand = await run(['--directory', given])
  byHand.child.kill(); await byHand.child.exited
  mkdirSync(join(parent, 'atlas-review-unsigned'))
  // A pid another process has taken since.
  mkdirSync(join(parent, 'atlas-review-reused'))
  writeFileSync(join(parent, 'atlas-review-reused', '.owner'), JSON.stringify({ pid: process.pid, started: '1', keep: false }))
  sweep(parent)
  expect(held()).toEqual([kept.directory.split('/').at(-1), 'atlas-review-unsigned'].sort())
})
