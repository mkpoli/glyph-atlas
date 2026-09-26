<script>
  import { page } from '$app/state'
  import Quiz from '$views/Quiz.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const inspector = useInspector(), session = useSession()
  const reading = $derived(page.url.searchParams.get('reading') || '')
</script>

<!-- A round is dealt for one reviewer, whose id exists only in the browser. -->
{#if session.state.clientId}{#key reading}<Quiz clientId={session.state.clientId} initialReading={reading} inspect={inspector.inspect.bind(inspector)} />{/key}{/if}
