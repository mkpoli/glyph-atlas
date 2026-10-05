<script>
  // A crop's address that cites another cut than the one shown: the cut it names, when it was recorded,
  // and that the crop has been cut again since; or that this crop has no cut by that name. The date is
  // the reader's own day, so it is written once the page is in the browser.
  import { onMount } from 'svelte'
  import { t, formatDate } from '../lib/i18n.svelte.js'
  import Glyph from './Glyph.svelte'
  let { cited } = $props()
  let failed = $state(''), mounted = $state(false)
  onMount(() => { mounted = true })
</script>

<div class="cited-cut" role="status">
  {#if cited.version?.image && failed !== cited.version.image}<Glyph item={{ image: cited.version.image }} alt={t('cite.cut.image')} class="cited-crop" onerror={() => failed = cited.version.image} />{/if}
  {#if cited.version}{#if mounted}<p>{t('cite.cut.earlier', { date: formatDate(cited.version.at) })}</p>{/if}{:else}<p>{t('cite.cut.missing')}</p>{/if}
</div>

<style>
  .cited-cut{display:flex;align-items:center;gap:12px;padding:10px 14px;margin-bottom:16px;border-radius:7px;background:var(--accent-light);color:var(--accent);font-size:12px;line-height:1.5}
  .cited-cut :global(.cited-crop){flex-shrink:0;width:48px;height:48px}
</style>
