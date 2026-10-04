<script>
  import Seo from '$components/Seo.svelte'
  import Favourites from '$views/Favourites.svelte'
  import { t } from '$lib/i18n.svelte.js'
</script>

<Seo title={t('favourites.title')} index={false} />

<Favourites />
