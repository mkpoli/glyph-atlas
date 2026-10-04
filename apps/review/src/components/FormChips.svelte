<script>
  // A grapheme's forms as a row of chips, each in its script's colour (a description marked as one),
  // the first ten with their number key. Quick Review's form bar marks a selection with them, and the
  // inspector marks its crop.
  import FormText from './FormText.svelte'
  import { t } from '../lib/i18n.svelte.js'
  let { forms = [], chosen = null, current = null, disabled = false, label = '', title = form => form, onchoose, children = null } = $props()
  const KEYS = '1234567890'
  const charOf = form => typeof form === 'string' ? form : form.char
</script>

<div class="form-chips" role="group" aria-label={label}>
  {#each forms as form, i (charOf(form))}
    {@const char = charOf(form)}
    <button type="button" class="form-chip" class:chosen={chosen === char} class:current={current === char && chosen == null} {disabled}
      aria-pressed={chosen === char || (current === char && chosen == null)} onclick={() => onchoose(char)} title={title(char)} aria-label={title(char)}>
      <FormText text={char} script={typeof form === 'string' ? '' : form.script} />{#if i < KEYS.length}<kbd>{KEYS[i]}</kbd>{/if}
    </button>
  {/each}
  {@render children?.()}
</div>

<style>
  .form-chips{display:flex;flex-wrap:wrap;gap:6px;flex:1;min-width:0}
  .form-chip{display:inline-flex;align-items:baseline;gap:6px;padding:4px 10px;font-size:22px;line-height:1.2;
    font-family:"Kureedo Kata","Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans",serif}
  .form-chip:not(:disabled):hover{border-color:var(--accent)}
  .form-chip.current{border-color:var(--line-strong)}
  .form-chip.chosen{border:2px solid var(--accent);padding:3px 9px;background:var(--accent-light)}
  .form-chip kbd{font-family:ui-monospace,monospace;font-size:9px;color:var(--muted)}
  @media(max-width:700px){.form-chip{font-size:19px;padding:3px 8px}.form-chip.chosen{padding:2px 7px}.form-chip kbd{display:none}}
  @media(hover:none){.form-chip kbd{display:none}}
</style>
