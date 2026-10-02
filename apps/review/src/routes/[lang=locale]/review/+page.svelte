<script>
  import Seo from '$components/Seo.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { page } from '$app/state'
  import Quiz from '$views/Quiz.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const inspector = useInspector(), session = useSession()
  const grapheme = $derived(page.url.searchParams.get('grapheme') || '')
</script>

<Seo title={t('nav.quickReview')} index={false} />

<!-- A round is dealt for one reviewer, whose id exists only in the browser. -->
{#if session.state.clientId}{#key grapheme}<Quiz clientId={session.state.clientId} initialGrapheme={grapheme} inspect={inspector.inspect.bind(inspector)} />{/key}{/if}
