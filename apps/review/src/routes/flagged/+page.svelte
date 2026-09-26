<script>
  import Explore from '$views/Explore.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  const inspector = useInspector(), session = useSession()
</script>

<Explore flagged inspect={inspector.inspect.bind(inspector)} ink={session.state.ink} onink={value => session.setInk(value)} onprogress={() => session.showProgress()} />
