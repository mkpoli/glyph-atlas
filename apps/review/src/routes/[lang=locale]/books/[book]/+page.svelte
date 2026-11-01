<script>
  import Seo from '$components/Seo.svelte'
  import { page } from '$app/state'
  import Book from '$views/Book.svelte'
  import { useSession } from '$lib/session.svelte.js'
  let { data } = $props()
  const session = useSession()
  const title = $derived(data.book?.title || page.params.book || '')
</script>

<Seo {title} index={false} />

{#if session.state.ready}<Book bookId={page.params.book ?? ''} initial={data.book} />{/if}
