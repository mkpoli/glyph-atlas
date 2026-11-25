import { afterAll, expect, it } from 'bun:test'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// A browser that writes into its profile for as long as it runs, as Chromium does, and never opens a
// debugging port; `exits` makes it stop at once.
const root = mkdtempSync(join(tmpdir(), 'browser-profile-test-')), chrome = join(root, 'chrome'), tmp = join(root, 'tmp')
mkdirSync(tmp)
writeFileSync(chrome, `#!/bin/sh
for arg; do case $arg in --user-data-dir=*) profile=\${arg#*=};; esac; done
echo $$ > "${root}/pid"
trap '' TERM
mkdir -p "$profile/Default"; [ -n "$EXITS" ] && exit 3
i=0; while [ $((i += 1)) -lt 2000 ]; do mkdir -p "$profile/Default" && : > "$profile/Default/Cookies"; sleep 0.01; done
`)
chmodSync(chrome, 0o755)
afterAll(() => rmSync(root, { recursive: true, force: true }))

const profiles = () => readdirSync(tmp)
const running = () => { try { return !/\bZ\b/.test(readFileSync(`/proc/${readFileSync(join(root, 'pid'), 'utf8').trim()}/stat`, 'utf8')) } catch { return false } }
const until = async (done) => { for (let i = 0; i < 100 && !done(); i++) await Bun.sleep(50); return done() }
function check(env = {}, detached = false) {
  return Bun.spawn([process.execPath, '-e', `const { default: Browser } = await import(${JSON.stringify(join(import.meta.dir, 'browser.mjs'))}); await Browser.launch({ chrome: ${JSON.stringify(chrome)} })`],
    { env: { ...process.env, TMPDIR: tmp, ...env }, stdout: 'ignore', stderr: 'ignore', detached })
}

for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP', 'SIGKILL']) for (const group of [false, true]) it(`leaves no profile and no browser after ${signal}${group ? ' to its group' : ''}`, async () => {
  const tool = check({}, group)
  expect(await until(() => profiles().length === 1 && existsSync(join(tmp, profiles()[0], 'Default')))).toBe(true)
  process.kill(group ? -tool.pid : tool.pid, signal)
  await tool.exited
  expect(await until(() => !profiles().length && !running())).toBe(true)
}, 20000)

it('leaves no profile where the browser does not start', async () => {
  expect(await check({ EXITS: '1' }).exited).not.toBe(0)
  expect(await until(() => !profiles().length)).toBe(true)
}, 20000)
