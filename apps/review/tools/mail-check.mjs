#!/usr/bin/env bun
/** Shoots the sign-in mail card for each language the mail is written in, for a look at the rendering. */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { signInMail } from '../../cloudflare/src/mail.ts'
import Browser from './browser.mjs'

const out = '/tmp/atlas-mail'
mkdirSync(out, { recursive: true })
const LOCALES = ['en', 'ja', 'ja-x-classical', 'ko', 'ko-Kore', 'lzh', 'vi', 'vi-Hani', 'zh-Hans', 'zh-Hant']
const browser = await Browser.launch({ width: 700, height: 620 })
try {
  for (const locale of LOCALES) {
    const { html } = signInMail(locale, '995110', 'glyphatlas.org')
    const file = join(out, `${locale}.html`)
    await Bun.write(file, html)
    // A mail body never hydrates, so `goto` (which waits for the app) cannot load it: navigate and
    // wait for the load event alone.
    const loaded = browser.once('Page.loadEventFired', 15000).catch(() => null)
    await browser.send('Page.navigate', { url: `file://${file}` })
    await loaded
    await browser.screenshot(join(out, `${locale}.png`))
    console.log(locale, '->', join(out, `${locale}.png`))
  }
} finally {
  await browser.close()
}
