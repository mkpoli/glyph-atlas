<script>
  import Gallery from '$views/Gallery.svelte'
  let { data } = $props()
</script>

<Gallery gallery={data.gallery} />
