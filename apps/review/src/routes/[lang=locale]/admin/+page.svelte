<script>
  import Seo from '$components/Seo.svelte'
  import Admin from '$views/Admin.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const session = useSession()
</script>

<Seo title={t('admin.title')} index={false} />

{#if session.state.ready}{#if session.state.user?.admin}<Admin />{:else}<div class="empty"><span class="empty-mark">∅</span><h2>{t('admin.only')}</h2></div>{/if}{/if}
