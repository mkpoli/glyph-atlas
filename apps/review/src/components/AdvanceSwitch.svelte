<script>
  import { t } from '../lib/i18n.svelte.js'
  import { useSession } from '../lib/session.svelte.js'
  // Whether a save, or a skip, goes on to the next crop of the list the inspector was opened from.
  // The labels of both buttons follow it, so the reader always sees where the next click leads.
  let { disabled = false } = $props()
  const session = useSession()
</script>

<button type="button" class="advance-switch" role="switch" aria-checked={session.state.advance} {disabled} onclick={() => session.setAdvance(!session.state.advance)}>
  <span class="advance-track" aria-hidden="true"><span class="advance-knob">→</span></span>
  <span class="advance-label">{t('character.advance.label')}</span>
</button>

<style>
  /* Off, the knob stays lighter than its track in both schemes, so the switch reads as a switch. */
  .advance-switch{display:inline-flex;align-items:center;gap:10px;border:0;background:transparent;padding:6px 2px;font-size:12px;color:var(--muted);white-space:nowrap}
  .advance-switch:not(:disabled):hover{color:var(--ink)}
  .advance-track{position:relative;flex-shrink:0;width:40px;height:22px;border-radius:11px;background:var(--surface-sunken);box-shadow:inset 0 0 0 1px light-dark(var(--line), #5a5866);transition:background .18s,box-shadow .18s}
  .advance-knob{position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:light-dark(var(--surface), #c9c7d2);box-shadow:0 1px 3px var(--shadow);display:flex;align-items:center;justify-content:center;font-size:10px;line-height:1;color:transparent;transition:transform .2s cubic-bezier(.3,1.4,.5,1),color .18s}
  .advance-switch[aria-checked="true"]{color:var(--ink)}
  .advance-switch[aria-checked="true"] .advance-track{background:var(--accent-solid);box-shadow:inset 0 0 0 1px var(--accent-solid)}
  .advance-switch[aria-checked="true"] .advance-knob{transform:translateX(18px);color:var(--accent-solid)}
  @media(prefers-reduced-motion:reduce){.advance-track,.advance-knob{transition:none}}
</style>
