import { test, expect } from 'bun:test'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { sourcePatch } from '../src/lib/source-patch.js'

for (const before of [null, '', '[{"original":"あ"}]', '[{"original":"あ"}]\n']) {
  test(`downloaded patch applies exactly (original ${JSON.stringify(before)})`, () => {
    const root = mkdtempSync(join(tmpdir(), 'atlas-patch-'))
    try {
      const file = { path: 'data/editorial/corrections/fixture/copy/p1.json', before,
        after: '[\n  {"original":"あ", "corrected":"い"}\n]\n' }
      const target = join(root, file.path)
      mkdirSync(dirname(target), { recursive: true })
      if (before !== null) writeFileSync(target, before)
      const patch = sourcePatch([file])
      const checked = spawnSync('git', ['apply', '--check', '-'], { cwd: root, input: patch, encoding: 'utf8' })
      expect(checked.status).toBe(0)
      const applied = spawnSync('git', ['apply', '-'], { cwd: root, input: patch, encoding: 'utf8' })
      expect(applied.status).toBe(0)
      expect(readFileSync(target, 'utf8')).toBe(file.after)
    } finally { rmSync(root, { recursive: true, force: true }) }
  })
}
