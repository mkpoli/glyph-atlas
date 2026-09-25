<script>
  import { scriptParts } from '../lib/identity.js'
  import { t } from '../lib/i18n.svelte.js'
  let { text = '', script = '' } = $props()
  // `scriptParts` returns a fixed English `label` for use outside a Svelte context (it is also
  // called directly by a plain-Node check); the label shown here is looked up again from `key` so
  // it follows the interface language.
  const parts = $derived(scriptParts(text, script).map(part => ({ ...part, label: t(`script.${part.key}`) })))
  const description = $derived(parts.map(part => `${part.text} · ${part.label}`).join(', '))
</script>

<span class="script-text" lang="ja" role="img" aria-label={description}>{#each parts as part, index (index)}<span class="script-char" data-script={part.key} lang={part.key === 'hangul' ? 'ko' : undefined} title={`${part.text} · ${part.label}`} aria-hidden="true">{part.text}</span>{/each}</span>
