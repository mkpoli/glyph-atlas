<script>
  import Seo from '$components/Seo.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { page } from '$app/state'
  import Quiz from '$views/Quiz.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const inspector = useInspector(), session = useSession()
  const reading = $derived(page.url.searchParams.get('reading') || '')
</script>

<Seo title={t('nav.quickReview')} index={false} />

<!-- A round is dealt for one reviewer, who is known once the page runs in a browser. -->
{#if session.state.ready}{#key reading}<Quiz initialReading={reading} inspect={inspector.inspect.bind(inspector)} />{/key}{/if}
