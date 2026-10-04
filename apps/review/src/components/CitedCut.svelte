<script>
  // A crop's address that cites another cut than the one shown: the cut it names, when it was recorded,
  // and that the crop has been cut again since; or that this crop has no cut by that name.
  import { t, formatDate } from '../lib/i18n.svelte.js'
  let { cited } = $props()
  let failed = $state(false)
</script>

<div class="cited-cut" role="status">
  {#if cited.version?.image && !failed}<img src={cited.version.image} alt={t('cite.cut.image')} onerror={() => failed = true} />{/if}
  <p>{cited.version ? t('cite.cut.earlier', { date: formatDate(cited.version.at) }) : t('cite.cut.missing')}</p>
</div>

<style>
  .cited-cut{display:flex;align-items:center;gap:12px;padding:10px 14px;margin-bottom:16px;border-radius:7px;background:var(--accent-light);color:var(--accent);font-size:12px;line-height:1.5}
  img{flex-shrink:0;width:48px;height:48px;object-fit:contain;border-radius:4px;background:var(--surface)}
</style>
