<script>
  import { t } from '../lib/i18n.svelte.js'
  let { character = '', compact = false } = $props()
  const target = $derived(typeof character === 'string' && [...character].length === 1 ? character : '')
</script>

{#if target}
  <a class="zi-link" class:compact href={`https://zi.tools/zi/${encodeURIComponent(target)}`}
     target="_blank" rel="noopener noreferrer" aria-label={t('ziLink.aria', { char: target })}
     onclick={event => event.stopPropagation()}>{t('ziLink.text')}</a>
{/if}

<style>
  .zi-link{font-size:11px;font-weight:400;white-space:nowrap;color:var(--muted);text-decoration:none;line-height:1.5}
  .zi-link:hover,.zi-link:focus-visible{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
  .compact{font-size:10px}
</style>
