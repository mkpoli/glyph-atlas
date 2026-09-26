#!/usr/bin/env bun
/**
 * A very small Chromium driver over the DevTools protocol: enough to load the built interface in a
 * real browser, click and drag on it, press its keys, and take screenshots.
 *
 * Playwright would be the usual tool, and its browsers are already in `~/.cache/ms-playwright`; the
 * driver below speaks the same protocol to the same binary without the download, which keeps the
 * disk budget small. Nothing here is specific to the review interface.
 */

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromePath } from './harness.mjs'

const KEY_CODES = {
  Enter: { code: 'Enter', keyCode: 13, text: '\r' },
  Escape: { code: 'Escape', keyCode: 27, text: '' },
  ' ': { code: 'Space', keyCode: 32, text: ' ' },
  ArrowUp: { code: 'ArrowUp', keyCode: 38, text: '' },
  ArrowDown: { code: 'ArrowDown', keyCode: 40, text: '' },
  ArrowLeft: { code: 'ArrowLeft', keyCode: 37, text: '' },
  ArrowRight: { code: 'ArrowRight', keyCode: 39, text: '' },
  '?': { code: 'Slash', keyCode: 191, text: '?' },
}

function keyDescription(key) {
  if (KEY_CODES[key]) return KEY_CODES[key]
  const upper = key.toUpperCase()
  const code = /^[a-z0-9]$/i.test(key) ? `Key${upper}` : key
  const keyCode = key.length === 1 ? upper.charCodeAt(0) : 0
  return { code, keyCode, text: key }
}

export class Browser {
  constructor(process, socket, directory) {
    this.process = process
    this.socket = socket
    this.directory = directory
    this.next = 1
    this.pending = new Map()
    this.listeners = []
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id)
        this.pending.delete(message.id)
        if (message.error) reject(new Error(`${message.error.message} (${JSON.stringify(message.error.data ?? '')})`))
        else resolve(message.result)
      } else if (message.method) {
        for (const listener of this.listeners) listener(message)
      }
    })
  }

  static async launch({ chrome = null, width = 1440, height = 900 } = {}) {
    const binary = chromePath(chrome)
    if (!binary) throw new Error('no Chromium found; pass --chrome /path/to/chrome')
    const directory = mkdtempSync(join(tmpdir(), 'atlas-chrome-'))
    const process_ = Bun.spawn(
      [
        binary,
        '--headless',
        '--no-sandbox',
        '--disable-gpu',
        '--disable-dev-shm-usage',
        '--hide-scrollbars',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-extensions',
        '--force-device-scale-factor=1',
        '--window-size=' + width + ',' + height,
        '--remote-debugging-port=0',
        `--user-data-dir=${directory}`,
        'about:blank',
      ],
      { stdout: 'pipe', stderr: 'pipe' },
    )
    const portFile = join(directory, 'DevToolsActivePort')
    const deadline = Date.now() + 30000
    while (!existsSync(portFile) && Date.now() < deadline) {
      if (process_.exitCode !== null) throw new Error(`chromium exited with ${process_.exitCode}`)
      await Bun.sleep(100)
    }
    if (!existsSync(portFile)) throw new Error('chromium did not open a debugging port')
    const port = readFileSync(portFile, 'utf8').split('\n')[0].trim()
    let target = null
    while (!target && Date.now() < deadline) {
      try {
        const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        target = targets.find((entry) => entry.type === 'page')
      } catch {
        /* the endpoint is not up yet */
      }
      if (!target) await Bun.sleep(100)
    }
    if (!target) throw new Error('chromium opened no page target')
    const socket = new WebSocket(target.webSocketDebuggerUrl)
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true })
      socket.addEventListener('error', () => reject(new Error('the DevTools socket failed')), { once: true })
    })
    const browser = new Browser(process_, socket, directory)
    await browser.send('Page.enable')
    await browser.send('Runtime.enable')
    // `visit(path)` moves within the app the way a link does, without reloading the page.
    await browser.send('Page.addScriptToEvaluateOnNewDocument', { source: `window.visit = path => {
      const link = Object.assign(document.createElement('a'), { href: path })
      document.body.append(link); link.click(); link.remove() }` })
    await browser.setViewport(width, height)
    return browser
  }

  send(method, params = {}) {
    const id = this.next++
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.socket.send(JSON.stringify({ id, method, params }))
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id)
          reject(new Error(`${method} timed out`))
        }
      }, 30000)
    })
  }

  /** Resolve on the next matching protocol event. */
  once(method, timeout = 30000) {
    return new Promise((resolve, reject) => {
      const listener = (message) => {
        if (message.method !== method) return
        this.listeners = this.listeners.filter((entry) => entry !== listener)
        resolve(message.params)
      }
      this.listeners.push(listener)
      setTimeout(() => {
        this.listeners = this.listeners.filter((entry) => entry !== listener)
        reject(new Error(`no ${method} within ${timeout} ms`))
      }, timeout)
    })
  }

  async setViewport(width, height) {
    await this.send('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: width < 500,
    })
  }

  async setColorScheme(scheme) {
    await this.send('Emulation.setEmulatedMedia', {
      features: [{ name: 'prefers-color-scheme', value: scheme }],
    })
  }

  /** Load a URL and wait until the app has hydrated it: before that, a click reaches no handler. */
  async goto(url, { waitFor = null, timeout = 30000 } = {}) {
    const loaded = this.once('Page.loadEventFired', timeout).catch(() => null)
    await this.send('Page.navigate', { url })
    await loaded
    await this.waitFor(`document.documentElement.dataset.hydrated !== undefined`, timeout)
    if (waitFor) await this.waitFor(waitFor, timeout)
    return this
  }

  /** Evaluate an expression in the page and return its JSON value. */
  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    })
    if (result.exceptionDetails) {
      throw new Error(`page error: ${result.exceptionDetails.exception?.description ?? result.exceptionDetails.text}`)
    }
    return result.result.value
  }

  /** Poll an expression until it is truthy. */
  async waitFor(expression, timeout = 20000, interval = 100) {
    const deadline = Date.now() + timeout
    let last = null
    while (Date.now() < deadline) {
      try {
        last = await this.evaluate(expression)
        if (last) return last
      } catch (error) {
        last = error.message
      }
      await Bun.sleep(interval)
    }
    throw new Error(`waitFor timed out: ${expression} (last: ${JSON.stringify(last)})`)
  }

  async key(key, { shift = false } = {}) {
    const description = keyDescription(key)
    const base = {
      modifiers: shift ? 8 : 0,
      key: key === ' ' ? ' ' : key,
      code: description.code,
      windowsVirtualKeyCode: description.keyCode,
      nativeVirtualKeyCode: description.keyCode,
    }
    await this.send('Input.dispatchKeyEvent', { type: 'keyDown', ...base, text: description.text })
    await this.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base })
    await Bun.sleep(60)
  }

  async click(x, y, { button = 'left', clickCount = 1 } = {}) {
    const common = { x: Math.round(x), y: Math.round(y), button, buttons: button === 'left' ? 1 : 0, clickCount }
    await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: common.x, y: common.y })
    await this.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...common })
    await this.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...common })
    await Bun.sleep(80)
  }

  /** Press, move in steps, release: what a drag of a box or a drawn rectangle is. */
  async drag(from, to, { steps = 8 } = {}) {
    await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(from.x), y: Math.round(from.y) })
    await this.send('Input.dispatchMouseEvent', {
      type: 'mousePressed',
      x: Math.round(from.x),
      y: Math.round(from.y),
      button: 'left',
      buttons: 1,
      clickCount: 1,
    })
    for (let step = 1; step <= steps; step += 1) {
      const x = from.x + ((to.x - from.x) * step) / steps
      const y = from.y + ((to.y - from.y) * step) / steps
      await this.send('Input.dispatchMouseEvent', {
        type: 'mouseMoved',
        x: Math.round(x),
        y: Math.round(y),
        button: 'left',
        buttons: 1,
      })
      await Bun.sleep(20)
    }
    await this.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased',
      x: Math.round(to.x),
      y: Math.round(to.y),
      button: 'left',
      buttons: 0,
      clickCount: 1,
    })
    await Bun.sleep(120)
  }

  /** The centre of the first element matching a selector. */
  async centre(selector) {
    const box = await this.evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)})
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, w: rect.width, h: rect.height }
    })()`)
    if (!box) throw new Error(`no element matches ${selector}`)
    return box
  }

  async screenshot(path, { fullPage = false } = {}) {
    const result = await this.send('Page.captureScreenshot', {
      format: 'png',
      captureBeyondViewport: fullPage,
      fromSurface: true,
    })
    writeFileSync(path, Buffer.from(result.data, 'base64'))
    return path
  }

  async close() {
    try {
      this.socket.close()
    } catch {
      /* already closed */
    }
    this.process.kill()
    rmSync(this.directory, { recursive: true, force: true })
  }
}

export default Browser
