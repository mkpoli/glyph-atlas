import { mount, unmount } from 'svelte'
import IdsCard from '../components/IdsCard.svelte'
import { readSequence } from './idsView.js'

const OPEN = 300, AGAIN = 60, CLOSE = 180, HOLD = 500
// The one card shown, `{ trigger, element, enter, hide }`, with `tabbed` where Tab reached it, what is
// about to open or close it, and whether the last key pressed was Tab: a page that puts the focus
// somewhere itself, as a board does on what was put down, opens nothing.
let shown = null, timer = null, tabbing = false
const wait = (run, ms) => { clearTimeout(timer); timer = setTimeout(run, ms) }
const still = () => clearTimeout(timer)
function hide() { still(); shown?.hide() }
// A card stays while the pointer is on it or on what it describes, while the focus is in it, or while
// it is on what Tab reached.
const kept = () => {
  const on = [shown?.trigger, shown?.element()]
  return on.some(part => part?.matches(':hover')) || on[1]?.contains(document.activeElement) || Boolean(shown?.tabbed && on[0] === document.activeElement)
}
const leave = () => wait(() => { if (!kept()) hide() }, CLOSE)

function show(trigger, { own, tabbed, ...props }) {
  still()
  if (shown?.trigger === trigger || !trigger.isConnected || trigger.closest('[data-ids-quiet]')) return
  hide()
  // Within an open dialog the card stands inside it: what is outside a modal dialog takes no pointer.
  const card = mount(IdsCard, { target: trigger.closest('dialog') ?? document.body, props: { ...props, trigger, stay: still, leave, hide } })
  if (own) trigger.setAttribute('aria-expanded', 'true')
  shown = { trigger, tabbed, element: card.element, enter: card.enter, hide() {
    shown = null
    if (own) trigger.setAttribute('aria-expanded', 'false')
    unmount(card)
  } }
}

if (typeof document !== 'undefined') {
  document.addEventListener('keydown', event => { tabbing = event.key === 'Tab' }, true)
  document.addEventListener('pointerdown', () => { tabbing = false }, true)
}

/**
 * Shows a description's card (`IdsCard`) for what it is attached to: on pointing at it or reaching it
 * from the keyboard, on a tap, and on holding a finger on it. Within a button or a link the whole
 * control is pointed at, and its own click stays its own; `inline` is for a description that is one
 * part of a larger control, pointed at alone. Standing by itself it is made a button of its own. `side`
 * puts the card beside it. A card stays shut under `data-ids-quiet`, as while a board is being dragged on.
 */
export const idsCard = (text, { lang = '', inline = false, side = false } = {}) => node => {
  if (!readSequence(text) || node.closest('[aria-hidden="true"]')) return
  const control = node.closest('button, a[href], summary')
  const trigger = control && !inline ? control : node, own = !control
  const stop = new AbortController(), on = (type, run, options = {}) => trigger.addEventListener(type, run, { signal: stop.signal, ...options })
  const open = (tabbed = false) => show(trigger, { text, lang, side, own, tabbed })
  let held = false, from = null
  const touch = event => event.pointerType === 'touch'

  if (own) {
    trigger.tabIndex = 0
    trigger.setAttribute('role', 'button')
    trigger.setAttribute('aria-expanded', 'false')
  }
  on('pointerenter', event => { if (!touch(event) && !event.buttons) wait(() => open(), shown ? AGAIN : OPEN) })
  on('pointerleave', event => { if (!touch(event)) leave() })
  on('pointerdown', event => {
    held = false
    if (touch(event)) { from = event; wait(() => { held = true; open() }, HOLD) }
    else if (!own) hide()
  })
  // A finger that scrolls or lifts early holds nothing.
  on('pointermove', event => { if (from && touch(event) && Math.hypot(event.clientX - from.clientX, event.clientY - from.clientY) > 10) { from = null; if (!held) still() } })
  for (const type of ['pointerup', 'pointercancel']) on(type, () => { if (from && !held) still(); from = null })
  on('contextmenu', event => { if (held) event.preventDefault() })
  on('click', event => {
    if (held) { held = false; event.preventDefault(); event.stopImmediatePropagation() }
    else if (own) { event.stopPropagation(); open() }
    // After the control has done what a tap on it does; a link goes where it leads.
    else if (touch(event) && trigger.tagName !== 'A') wait(() => open(), 0)
    else hide()
  }, { capture: true })
  if (trigger === control || own) {
    on('focus', () => { if (tabbing) wait(() => open(true), shown ? AGAIN : OPEN) })
    on('blur', leave)
    on('keydown', event => {
      if (event.key === 'Tab' && !event.shiftKey && shown?.trigger === trigger) { event.preventDefault(); shown.enter() }
      else if (own && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); event.stopPropagation(); open() }
    })
  }
  return () => {
    stop.abort()
    if (shown?.trigger === trigger) hide()
  }
}
