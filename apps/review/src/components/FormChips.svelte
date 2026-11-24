<script>
  // A grapheme's forms as a row of chips, each in its script's colour (a description marked as one),
  // the first ten with their number key. Quick Review's form bar marks a selection with them, and the
  // inspector marks its crop. `groups` sets the chips under a caption each, the 構形 its forms share
  // (`{ id, label, forms, own }`, `own` where the crop's own form is built so, a form `proposed` there
  // marked); the number keys follow the chips as they stand.
  import FormText from './FormText.svelte'
  let { forms = [], groups = null, chosen = null, current = null, disabled = false, label = '', title = form => form, onchoose, children = null } = $props()
  const KEYS = '1234567890'
  const charOf = form => typeof form === 'string' ? form : form.char
  const place = $derived(new Map((groups ? groups.flatMap(group => group.forms) : forms).map((form, i) => [charOf(form), i])))
</script>

{#snippet chip(form)}
  {@const char = charOf(form)}
  <button type="button" class="form-chip" class:chosen={chosen === char} class:current={current === char && chosen == null} {disabled}
    aria-pressed={chosen === char || (current === char && chosen == null)} onclick={() => onchoose(char)} title={title(char)} aria-label={title(char)}>
    <FormText text={char} script={typeof form === 'string' ? '' : form.script} lang={typeof form === 'string' ? '' : form.lang} />{#if form.proposed}<sup aria-hidden="true">*</sup>{/if}{#if place.get(char) < KEYS.length}<kbd>{KEYS[place.get(char)]}</kbd>{/if}
  </button>
{/snippet}

<div class="form-chips" class:grouped={Boolean(groups)} role="group" aria-label={label}>
  {#if groups}
    {#each groups as group (group.id)}
      <div class="form-group" class:own={group.own} role={group.label ? 'group' : undefined} aria-label={group.label || undefined}>
        {#if group.label}<span class="form-group-label" lang="zh" aria-hidden="true">{group.label}</span>{/if}
        <div class="form-group-chips">{#each group.forms as form (charOf(form))}{@render chip(form)}{/each}</div>
      </div>
    {/each}
  {:else}
    {#each forms as form (charOf(form))}{@render chip(form)}{/each}
  {/if}
  {@render children?.()}
</div>

<style>
  .form-chips{display:flex;flex-wrap:wrap;gap:6px;flex:1;min-width:0}
  .form-chips.grouped{gap:8px 14px;align-items:flex-end}
  .form-group{display:flex;flex-direction:column;gap:3px;min-width:0}
  .form-group-label{font-size:11px;line-height:1.3;color:var(--muted)}
  .form-group.own .form-group-label{color:var(--ink)}
  .form-group-chips{display:flex;flex-wrap:wrap;gap:6px}
  .form-chip{position:relative;display:inline-flex;align-items:baseline;gap:6px;padding:4px 10px;font-size:22px;line-height:1.2;
    font-family:var(--char-font)}
  .form-chip:not(:disabled):hover{border-color:var(--accent)}
  .form-chip.current{border-color:var(--line-strong)}
  .form-chip.chosen{border:2px solid var(--accent);padding:3px 9px;background:var(--accent-light)}
  .form-chip sup{position:absolute;top:4px;right:4px;font-size:11px;line-height:1;color:var(--muted)}
  .form-chip kbd{font-family:ui-monospace,monospace;font-size:9px;color:var(--muted)}
  @media(max-width:700px){.form-chip{font-size:19px;padding:3px 8px}.form-chip.chosen{padding:2px 7px}.form-chip kbd{display:none}}
  @media(hover:none){.form-chip kbd{display:none}}
</style>
