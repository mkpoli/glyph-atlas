<script>
  import Seo from '$components/Seo.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import Books from '$views/Books.svelte'
  import { useSession } from '$lib/session.svelte.js'
  let { data } = $props()
  const session = useSession()
</script>

<Seo title={t('books.heading')} index={false} />

{#if session.state.ready}<Books initialQuery={data.query} />{/if}
