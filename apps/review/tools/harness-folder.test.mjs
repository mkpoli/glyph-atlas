import { afterAll, expect, test } from 'bun:test'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
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

test('a run told to stop while its dataset is being built takes the build with it', async () => {
  const build = join(parent, 'build'), pid = join(parent, 'pid')
  writeFileSync(build, `#!${process.execPath}
import { mkdirSync, renameSync, writeFileSync } from 'node:fs'
writeFileSync('${pid}.part', String(process.pid)); renameSync('${pid}.part', '${pid}')
for (;;) { mkdirSync(process.argv[3], { recursive: true }); writeFileSync(process.argv[3] + '/page', '') }
`)
  chmodSync(build, 0o755)
  const child = Bun.spawn([process.execPath, '-e', `const { boot, options } = await import(${JSON.stringify(join(import.meta.dir, 'harness.mjs'))})
    const config = options(['--python', ${JSON.stringify(build)}])
    console.log(config.directory); await boot(config)`], { env: { ...process.env, TMPDIR: parent }, stdout: 'pipe', stderr: 'inherit' })
  const { value } = await child.stdout.getReader().read(), directory = new TextDecoder().decode(value).trim()
  while (!existsSync(pid) || !existsSync(join(directory, 'page'))) await Bun.sleep(10)
  const builder = Number(readFileSync(pid, 'utf8'))
  const alive = () => { try { process.kill(builder, 0); return true } catch { return false } }
  child.kill('SIGTERM')
  const code = await child.exited
  await Bun.sleep(300)
  const left = [alive(), existsSync(directory)]
  if (left[0]) process.kill(builder, 'SIGKILL')
  await Bun.sleep(50)
  for (const path of [build, pid, directory]) rmSync(path, { recursive: true, force: true })
  expect([code, ...left]).toEqual([143, false, false])
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
