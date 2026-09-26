<script>
  import { page } from '$app/state'
  import Seo from '$components/Seo.svelte'
  import { t, locale } from '$lib/i18n.svelte.js'
  import Explore from '$views/Explore.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  let { data } = $props()
  const inspector = useInspector(), session = useSession()
</script>

<Seo data={{ '@type': 'WebSite', name: t('app.name'), url: page.url.origin + '/', inLanguage: locale() }} />

<Explore initial={data.explore} inspect={inspector.inspect.bind(inspector)} ink={session.state.ink} onink={value => session.setInk(value)} onprogress={() => session.showProgress()} />
