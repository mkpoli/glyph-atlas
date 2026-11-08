<script>
  import { onMount } from 'svelte'
  import { issueTitle, issueHint } from '../lib/issues.js'
  import { t } from '../lib/i18n.svelte.js'

  let { issue } = $props()
  const id = $props.id()
  let trigger, popup
  let open = $state(false)
  let pinned = false
  let returningFocus = false
  let timer

  function position() {
    const scrollTop = popup.scrollTop
    popup.style.maxHeight = ''
    const anchor = trigger.getBoundingClientRect()
    const box = popup.getBoundingClientRect()
    const left = Math.max(8, Math.min(anchor.left, window.innerWidth - box.width - 8))
    const below = anchor.bottom + 6
    const spaceBelow = Math.max(0, window.innerHeight - 8 - below)
    const spaceAbove = Math.max(0, anchor.top - 6 - 8)
    const placeBelow = box.height <= spaceBelow || (box.height > spaceAbove && spaceBelow >= spaceAbove)
    popup.style.maxHeight = `${placeBelow ? spaceBelow : spaceAbove}px`
    popup.scrollTop = scrollTop
    const height = popup.getBoundingClientRect().height
    const top = placeBelow ? below : anchor.top - height - 6
    popup.style.left = `${left}px`
    popup.style.top = `${top}px`
  }
  function show() {
    clearTimeout(timer)
    if (!popup.matches(':popover-open')) popup.showPopover()
    position()
    open = true
  }
  function hide() {
    clearTimeout(timer)
    pinned = false
    if (popup?.matches(':popover-open')) popup.hidePopover()
    open = false
  }
  function leave() {
    clearTimeout(timer)
    timer = setTimeout(() => { if (!pinned && document.activeElement !== trigger && !popup.contains(document.activeElement)) hide() }, 120)
  }
  function toggle() {
    if (pinned) hide()
    else { pinned = true; show() }
  }
  onMount(() => {
    function escape(event) {
      if (event.key === 'Escape' && popup.matches(':popover-open')) {
        event.preventDefault()
        event.stopPropagation()
        const returnFocus = popup.contains(document.activeElement)
        hide()
        if (returnFocus) {
          returningFocus = true
          trigger.focus()
          returningFocus = false
        }
      }
    }
    function reposition(event) {
      if (event.type === 'scroll' && event.target instanceof Node && popup.contains(event.target)) return
      if (popup.matches(':popover-open')) position()
    }
    document.addEventListener('keydown', escape, true)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('keydown', escape, true)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  })
</script>

<button bind:this={trigger} type="button" class="issue-help" aria-label={t('issue.example.help', { issue: issueTitle(issue.id) })} aria-haspopup="dialog" aria-controls={`${id}-example`} aria-expanded={open}
  onpointerenter={event => { if (event.pointerType === 'mouse') show() }} onpointerleave={event => { if (event.pointerType === 'mouse') leave() }}
  onfocus={() => { if (!returningFocus) show() }} onblur={leave} onclick={toggle}>?</button>
<div bind:this={popup} id={`${id}-example`} class="issue-popover" popover="auto" role="dialog" tabindex="-1" aria-label={issueTitle(issue.id)}
  onpointerenter={() => clearTimeout(timer)} onpointerleave={leave} onfocusout={leave} ontoggle={event => { open = event.newState === 'open'; if (!open) pinned = false }}>
  <strong>{issueTitle(issue.id)}</strong>
  <span>{issueHint(issue.id)}</span>
  {#each issue.examples as example}
    <figure class="issue-popover-example">
      <div class="issue-popover-frame">
        <img src={`/issue-examples/${example.image}`} alt="" />
      </div>
      <figcaption>{t(`issue.example.${issue.id}.${example.case}`)}</figcaption>
    </figure>
  {/each}
  <small>{t('issue.example.notice')} <a href="https://codh.rois.ac.jp/char-shape/book/200021851/manifest.json" lang="ja" target="_blank" rel="noopener">日本古典籍データセット（国文研所蔵）CODH配信</a> · <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="license noopener">CC BY-SA 4.0</a></small>
</div>
