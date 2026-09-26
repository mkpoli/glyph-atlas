<script>
  import Seo from '$components/Seo.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { page } from '$app/state'
  import Pages from '$views/Pages.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const inspector = useInspector(), session = useSession()
</script>

<Seo title={t('nav.pages')} index={false} />

{#if session.state.clientId}<Pages clientId={session.state.clientId} pageId={page.params.id ?? ''} inspect={inspector.inspect.bind(inspector)} />{/if}
