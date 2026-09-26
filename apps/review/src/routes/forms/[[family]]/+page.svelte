<script>
  import Forms from '$views/Forms.svelte'
  let { data } = $props()
</script>

<Forms initial={data.initial} />
