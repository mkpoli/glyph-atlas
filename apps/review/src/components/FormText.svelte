<script>
  // A form as the picker and the form bar show it: a character in its script's colour, or a shape
  // Unicode lacks as its ideographic description sequence, marked as one. The sequence is the form's
  // name; a drawing of it is stored under `drawingKey(text)` (ids/<sha256 of NFC>.svg) and takes the
  // text's place once one exists for it.
  import ScriptText from './ScriptText.svelte'
  import { isDescription } from '../lib/ids.js'
  import { t } from '../lib/i18n.svelte.js'
  let { text = '', script = '' } = $props()
  const described = $derived(isDescription(text))
</script>

{#if described}
  <span class="ids-form" title={t('form.ids.named', { ids: text })}><span class="ids-text" lang="ja">{text}</span><small class="ids-tag">{t('form.ids.tag')}</small></span>
{:else}
  <ScriptText {text} {script} />
{/if}

<style>
  .ids-form{display:inline-flex;align-items:baseline;gap:4px}
  .ids-text{font-size:0.8em;letter-spacing:-0.02em}
  .ids-tag{font-family:ui-monospace,monospace;font-size:9px;letter-spacing:0.04em;color:var(--muted);border:1px solid var(--line);border-radius:3px;padding:0 3px}
</style>
