<script>
  import { scriptParts } from '../lib/identity.js'
  let { text = '', script = '' } = $props()
  const parts = $derived(scriptParts(text, script))
  const description = $derived(parts.map(part => `${part.text} · ${part.label}`).join(', '))
</script>

<span class="script-text" role="img" aria-label={description}>{#each parts as part, index (index)}<span class="script-char" data-script={part.key} title={`${part.text} · ${part.label}`} aria-hidden="true">{part.text}</span>{/each}</span>
