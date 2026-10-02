<script>
  import Seo from '$components/Seo.svelte'
  import Ranking from '$views/Ranking.svelte'
  import { t } from '$lib/i18n.svelte.js'
</script>

<Seo title={t('ranking.title')} />

<Ranking />
