<script>
  import Seo from '$components/Seo.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import History from '$views/History.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  const inspector = useInspector()
</script>

<Seo title={t('nav.history')} index={false} />

<History inspect={inspector.inspect.bind(inspector)} />
