import { describe, expect, it } from 'bun:test'
import { plugin } from 'bun'
import { readdirSync, readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { compile, compileModule } from 'svelte/compiler'
import { render } from 'svelte/server'

const LOCALE_DIR = join(import.meta.dir, '../src/locales')
const localeEntries = Object.fromEntries(
  readdirSync(LOCALE_DIR)
    .filter(name => name.endsWith('.json'))
    .map(name => [`../locales/${name}`, JSON.parse(readFileSync(join(LOCALE_DIR, name), 'utf8'))]),
)

plugin({
  name: 'review-svelte-test-loader',
  setup(build) {
    build.onLoad({ filter: /\.svelte\.js$/ }, async ({ path }) => {
      let source = await readFile(path, 'utf8')
      if (source.includes("import.meta.glob('../locales/*.json'")) {
        source = source.replace(
          "import.meta.glob('../locales/*.json', { eager: true, import: 'default' })",
          JSON.stringify(localeEntries),
        )
      }
      const { js } = compileModule(source, { filename: path, generate: 'server' })
      return { contents: js.code, loader: 'js' }
    })
    build.onLoad({ filter: /\.svelte$/ }, async ({ path }) => ({
      contents: compile(await readFile(path, 'utf8'), { filename: path, generate: 'server' }).js.code,
      loader: 'js',
    }))
  },
})

const { formatCompactNumber, useLocale } = await import('../src/lib/i18n.svelte.js')
const { default: ScriptStats } = await import('../src/components/ScriptStats.svelte')

describe('formatCompactNumber', () => {
  it('keeps counts below 10,000 exact and compacts larger counts per locale', () => {
    useLocale('en')
    expect(formatCompactNumber(0)).toBe('0')
    expect(formatCompactNumber(9999)).toBe('9,999')
    expect(formatCompactNumber(10000)).toBe('10K')
    expect(formatCompactNumber(2235740)).toBe('2.2M')

    useLocale('ja')
    expect(formatCompactNumber(9959)).toBe('9,959')
    expect(formatCompactNumber(2235740)).toMatch(/^223\.6万$/)

    useLocale('lzh')
    expect(formatCompactNumber(9959)).toBe('九千九百五十九')
    expect(formatCompactNumber(10000)).toBe('一萬')
    expect(formatCompactNumber(2235740)).toBe('二百二十四萬')
    expect(formatCompactNumber(-10000)).toBe('負一萬')
  })
})

describe('ScriptStats', () => {
  it('renders its heading and loading state on the server', () => {
    useLocale('en')
    const { body } = render(ScriptStats)
    expect(body).toContain('By script')
    expect(body).toContain('Loading script counts')
  })
})
