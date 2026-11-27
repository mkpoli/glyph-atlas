<script>
  import { onMount, tick } from 'svelte'
  import { placeBeside } from '../lib/popoverPlace.js'
  import { t } from '../lib/i18n.svelte.js'

  const id = $props.id()
  let trigger, popup
  let open = $state(false)

  const position = () => { if (popup && trigger) placeBeside(popup, trigger) }
  function show() {
    if (!popup) return
    if (typeof popup.showPopover === 'function') {
      if (!popup.matches(':popover-open')) popup.showPopover()
      position()
    } else {
      open = true
      tick().then(position)
    }
  }
  function hide() {
    if (!popup) return
    if (typeof popup.hidePopover === 'function') {
      if (popup.matches(':popover-open')) popup.hidePopover()
    } else {
      open = false
    }
  }
  function toggle() {
    if (open) hide()
    else show()
  }

  onMount(() => {
    function escape(event) {
      if (event.key === 'Escape' && open) {
        event.preventDefault()
        event.stopPropagation()
        hide()
        trigger?.focus()
      }
    }
    function reposition(event) {
      if (event.type === 'scroll' && event.target instanceof Node && popup?.contains(event.target)) return
      if (open) position()
    }
    document.addEventListener('keydown', escape, true)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      document.removeEventListener('keydown', escape, true)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  })
</script>

<button
  bind:this={trigger}
  type="button"
  class="quiz-guide-toggle"
  aria-label={t('quiz.guide.label')}
  title={t('quiz.guide.label')}
  aria-haspopup="dialog"
  aria-controls={`${id}-guide`}
  aria-expanded={open}
  onclick={toggle}
>?</button>

<div
  bind:this={popup}
  id={`${id}-guide`}
  class="quiz-guide-popover"
  class:is-open={open}
  popover="auto"
  role="dialog"
  tabindex="-1"
  aria-label={t('quiz.guide.label')}
  ontoggle={event => { open = event.newState === 'open'; if (open) position() }}
>
  <strong>{t('quiz.guide.label')}</strong>
  <ol>
    <li>{t('quiz.guide.step1')}</li>
    <li>{t('quiz.guide.step2')}</li>
    <li>{t('quiz.guide.step3')}</li>
  </ol>
</div>

<style>
  .quiz-guide-toggle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    padding: 0;
    border: 1px solid var(--line);
    border-radius: 50%;
    background: var(--surface);
    color: var(--muted);
    font-size: 12px;
    font-weight: 600;
    line-height: 1;
    flex-shrink: 0;
  }
  .quiz-guide-toggle:hover,
  .quiz-guide-toggle[aria-expanded="true"] {
    border-color: var(--accent);
    background: var(--accent-hover);
    color: var(--accent);
  }
  .quiz-guide-popover:is(:popover-open, .is-open) {
    position: fixed;
    inset: auto;
    margin: 0;
    width: 320px;
    max-width: calc(100vw - 16px);
    max-height: calc(100vh - 16px);
    overflow: auto;
    padding: 14px 16px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--surface);
    color: var(--ink);
    box-shadow: 0 8px 28px var(--shadow-menu);
    font-size: 12px;
    line-height: 1.5;
    z-index: 10;
  }
  @supports not selector(:popover-open) {
    .quiz-guide-popover:not(.is-open) { display: none; }
  }
  .quiz-guide-popover strong {
    display: block;
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .quiz-guide-popover ol {
    margin: 0;
    padding-inline-start: 18px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    color: var(--ink-soft, var(--muted));
  }
</style>
