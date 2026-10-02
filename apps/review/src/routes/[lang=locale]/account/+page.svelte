<script>
  import Seo from '$components/Seo.svelte'
  import Account from '$views/Account.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const session = useSession()
</script>

<Seo title={t('account.title')} index={false} />

<!-- Who is signed in is known once the page runs in a browser. -->
{#if session.state.ready}<Account />{/if}
