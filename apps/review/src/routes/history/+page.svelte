<script>
  import History from '$views/History.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const inspector = useInspector(), session = useSession()
</script>

<!-- "Only mine" needs the reviewer id, which exists only in the browser. -->
{#if session.state.clientId}<History clientId={session.state.clientId} inspect={inspector.inspect.bind(inspector)} />{/if}
