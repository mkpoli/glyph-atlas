<script>
  import Seo from '$components/Seo.svelte'
  import { t } from '$lib/i18n.svelte.js'
  import { page } from '$app/state'
  import Pages from '$views/Pages.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  let { data } = $props()
  const inspector = useInspector(), session = useSession()
  const record = $derived(data.record)
  const title = $derived([record?.document_title, record && t('pages.pageNumber', { number: record.seq + 1 })].filter(Boolean).join(' · ') || t('nav.books'))
</script>

<Seo {title} index={Boolean(record)} />

{#if session.state.ready}<Pages pageId={page.params.id ?? ''} focus={page.url.searchParams.get('crop') ?? ''}
  initial={record} inspect={inspector.inspect.bind(inspector)} />{/if}
