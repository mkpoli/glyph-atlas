<script>
  import { page } from '$app/state'
  import Seo from '$components/Seo.svelte'
  import { cropImage, holderOf, sourceTitle } from '$lib/seo.js'
  import { isUnassigned } from '$lib/identity.js'
  import { t } from '$lib/i18n.svelte.js'
  import Explore from '$views/Explore.svelte'
  import { useInspector } from '$lib/inspector.svelte.js'
  import { useSession } from '$lib/session.svelte.js'
  let { data } = $props()
  const inspector = useInspector(), session = useSession()
  const record = $derived(data.record)
  const label = $derived(isUnassigned(record) ? t('corpus.unassigned') : record.label)
  const source = $derived(sourceTitle(record))
  const image = $derived(record.image && record.proxyable !== false ? new URL(record.image, page.url.origin).href : null)
  const description = $derived([source ? t('meta.crop.description', { character: label, source }) : label,
    holderOf(record), record.licence].filter(Boolean).join(' · '))
</script>

<Seo title={source ? `${label} · ${source}` : label} {description} {image} type="article"
     data={image ? cropImage(record, image) : null} />

<!-- The crop itself is drawn by the layout's inspector, over the collection. -->
<Explore inspect={inspector.inspect.bind(inspector)} ink={session.state.ink} onink={value => session.setInk(value)} onprogress={() => session.showProgress()} />
