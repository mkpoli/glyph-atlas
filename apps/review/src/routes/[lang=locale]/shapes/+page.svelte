<script>
  import Seo from '$components/Seo.svelte'
  import Shapes from '$views/Shapes.svelte'
  import { t } from '$lib/i18n.svelte.js'
</script>

<Seo title={t('shapes.title')} index={false} />

<Shapes />
