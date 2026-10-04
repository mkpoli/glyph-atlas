<script>
  // A star for the crop on show; starred crops are listed on the favourites page.
  import { isFavourite, setFavourite } from '../lib/favourites.svelte.js'
  import { t } from '../lib/i18n.svelte.js'
  let { id } = $props()
  const starred = $derived(isFavourite(id))
  let failed = $state('')
  async function toggle() {
    failed = ''
    try { await setFavourite(id, !starred) } catch (error) { failed = error.message }
  }
</script>

<button type="button" class="icon-button favourite" class:starred aria-pressed={starred} title={failed || (starred ? t('favourites.remove') : t('favourites.add'))}
        aria-label={starred ? t('favourites.remove') : t('favourites.add')} onclick={toggle}>
  <svg viewBox="0 0 24 24" fill={starred ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>
</button>

<style>
  .favourite{color:var(--muted);padding:6px}
  .favourite svg{width:20px;height:20px}
  .favourite:hover{color:var(--ink)}
  .favourite.starred{color:var(--accent)}
</style>
