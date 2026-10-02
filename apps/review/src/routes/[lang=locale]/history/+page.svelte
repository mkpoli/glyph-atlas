<script>
  import Seo from '$components/Seo.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import History from '$views/History.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const inspector = useInspector(), session = useSession()
</script>

<Seo title={t('nav.history')} index={false} />

<!-- "Only mine" needs to know who is signed in, which is known once the page runs in a browser. -->
{#if session.state.ready}<History inspect={inspector.inspect.bind(inspector)} />{/if}
