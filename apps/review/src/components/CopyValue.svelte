<script>
  // A small button that copies one value shown beside it: a character, a form, a grapheme. It shows
  // while the pointer is over the value's `.copyable` group or the button has focus, and always on a
  // touch screen.
  import { t } from '../lib/i18n.svelte.js'
  let { value = '' } = $props()
  let copied = $state(false), timer
  // Stepping to another crop shows another value, which nobody has copied yet.
  $effect(() => { void value; copied = false; clearTimeout(timer) })
  async function copy(event) {
    event.stopPropagation()
    try { await navigator.clipboard.writeText(value) } catch { return }
    copied = true; clearTimeout(timer); timer = setTimeout(() => copied = false, 1500)
  }
</script>

{#if value}
  <button type="button" class="copy-value" class:copied aria-label={t('copyValue.aria', { value })} title={copied ? t('copyId.copied') : t('copyValue.aria', { value })} onclick={copy}>
    {#if copied}<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" /></svg>
    {:else}<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" /><path d="M10.5 3.5v-.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h.5" /></svg>{/if}
  </button>
  <span class="copy-status" role="status">{copied ? t('copyId.copied') : ''}</span>
{/if}

<style>
  .copy-value{position:relative;display:inline-flex;align-items:center;justify-content:center;flex:none;width:26px;height:26px;padding:0;
    border:0;border-radius:6px;background:none;color:var(--muted);cursor:copy;opacity:0;transition:opacity .12s}
  .copy-value svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.4;stroke-linecap:round;stroke-linejoin:round}
  .copy-value:hover,.copy-value:focus-visible{color:var(--accent);background:color-mix(in srgb, var(--accent) 12%, transparent)}
  .copy-value.copied{color:var(--accent)}
  :global(.copyable:hover) .copy-value,.copy-value:focus-visible,.copy-value.copied{opacity:1}
  @media(hover:none){.copy-value{opacity:.7}}
  .copy-status{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
</style>
