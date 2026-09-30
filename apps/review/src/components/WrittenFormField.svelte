<script>
  import { request } from '../lib/client.js'
  import { t } from '../lib/i18n.svelte.js'
  import { character as layerCharacter } from '../lib/layers.js'
  // The one rule for what may be typed, shared with the Worker and the review service: an
  // Ideographic Description Sequence is checked by its operators' arity, so a second check here
  // would drift from the one that saves.
  import { formProblem } from '../../../cloudflare/src/writtenForm'

  // What the crop's letterforms are written as, beside the style it is shown with. The value shown
  // is the recorded form, or the crop's own label while it has none. Opening the picker lists the
  // label's variants — the 異体字 graph the character page's Variants section reads — and takes
  // anything typed: one character, or a description such as ⿺辶𦊷 for a shape Unicode does not
  // encode. The save moves the form alone: character, grapheme, revision and verdict stay as they
  // are. `saved` receives the record after the write; `working` holds the dialog's own save, as the
  // style field's does, so the two never race.
  let { item, clientId, corpus = false, editable = true, disabled = false, working = null, saved } = $props()
  // `variants` names the character its list belongs to, so a list that arrives after the dialog has
  // moved to another crop is never shown for it.
  let picker, typed = $state(''), error = $state(''), variants = $state({ point: null, list: [] }), loading = $state(false)

  const crop = $derived(item?.id)
  const identity = $derived(item?.written_character ?? item?.label ?? '')
  const shown = $derived(item?.written_form ?? identity)
  // The 異体字 graph is keyed on one character; a longer label has no single entry to look up.
  const point = $derived([...identity].length === 1
    ? 'U+' + identity.codePointAt(0).toString(16).toUpperCase().padStart(4, '0') : null)
  const value = $derived(typed.trim())
  const problem = $derived(value ? formProblem(value) : null)
  const listed = $derived(variants.point === point ? variants.list : [])

  // A dialog stepped to another crop starts the picker again rather than carrying typed text over.
  $effect(() => { crop; typed = ''; error = '' })

  async function load() {
    const wanted = point
    if (!wanted || variants.point === wanted) return
    loading = true
    try {
      const card = await layerCharacter(wanted)
      // The variants a gallery widens to first, then the other related characters (simplified, …).
      variants = { point: wanted, list: [...card?.variants?.items ?? [], ...card?.variants?.related ?? []] }
    } catch {
      // The list is a convenience and the field still takes a typed form; the next open tries again.
    } finally { loading = false }
  }

  function opened(event) {
    if (event.currentTarget.open) load()
  }

  async function save(form) {
    working?.(true); error = ''
    try {
      const body = corpus
        ? { id: crypto.randomUUID(), identity: item.id, client_id: clientId, revision: item.revision,
            source_revision: item.source_revision, form }
        : { id: crypto.randomUUID(), client_id: clientId, revision: item.revision,
            image_sha256: item.image_sha256, form }
      const result = await request(corpus ? '/atlas/corpus/written-forms'
        : `/atlas/characters/${encodeURIComponent(item.id)}/written-form`, body)
      typed = ''
      if (picker) picker.open = false
      saved?.(result)
    } catch (e) { error = e.message } finally { working?.(false) }
  }

  function apply() {
    if (value && !problem) save(value)
  }
</script>

{#if item?.label}
  <div class="form-field">
    {#if editable}
    <details class="form-picker" bind:this={picker} ontoggle={opened}>
      <summary class="form-summary">
        <span class="form-value">{t('written.label')}: <b lang="ja">{shown}</b></span>
        <span class="form-change">{t('written.change')} <span aria-hidden="true">▾</span></span>
      </summary>
      <div class="form-panel">
        {#if point}
          <span class="form-group">{t('chips.variants')}</span>
          {#if loading}<span class="form-pending">…</span>
          {:else if listed.length}
            <div class="form-variants">
              {#each listed as variant (variant.code_point)}
                <button type="button" class="form-variant" class:active={item.written_form === variant.char}
                  {disabled} title={variant.code_point} lang="ja"
                  onclick={() => save(variant.char)}>{variant.char}</button>
              {/each}
            </div>
          {/if}
        {/if}
        <label class="form-input">{t('written.input')}
          <input bind:value={typed} maxlength="128" spellcheck="false" {disabled}
            onkeydown={event => { if (event.key === 'Enter') { event.preventDefault(); apply() } }} />
        </label>
        {#if problem}<span class="form-error" role="alert">{t(`written.problem.${problem}`)}</span>{/if}
        <p class="form-note">{t('written.note')}</p>
        <div class="form-actions">
          <button type="button" class="form-apply" disabled={disabled || !value || !!problem}
            onclick={apply}>{t('written.save')}</button>
          {#if item.written_form}<button type="button" class="quiet-link form-clear" {disabled}
            onclick={() => save(null)}>{t('common.clear')}</button>{/if}
        </div>
        {#if error}<span class="form-error" role="alert">{error}</span>{/if}
      </div>
    </details>
    {:else}
      <span class="form-value">{t('written.label')}: <b lang="ja">{shown}</b></span>
    {/if}
  </div>
{/if}

<style>
  .form-field{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;font-size:12px;color:var(--muted)}
  .form-picker{flex-basis:100%}
  .form-summary{display:flex;gap:12px;align-items:center;cursor:pointer;width:fit-content;list-style:none}
  .form-summary::-webkit-details-marker{display:none}
  .form-value b{color:var(--ink);font-weight:600}
  .form-change{font-size:11px;color:var(--muted);display:inline-flex;gap:4px;align-items:center}
  .form-picker[open] .form-change span{transform:rotate(180deg)}
  .form-panel{display:flex;flex-direction:column;gap:9px;align-items:flex-start;padding-top:10px}
  .form-group{font-size:11px}
  .form-pending{color:var(--muted)}
  .form-variants{display:flex;flex-wrap:wrap;gap:6px}
  .form-variant{min-width:36px;height:34px;padding:2px 8px;font-size:17px;line-height:1}
  .form-variant.active{border-color:var(--accent);color:var(--accent);background:var(--accent-light)}
  .form-input{display:flex;align-items:center;gap:10px;width:100%;font-size:11px}
  .form-input input{flex:1;font-size:15px;padding:7px 10px}
  .form-note{margin:0;font-size:10px;color:var(--muted)}
  .form-actions{display:flex;gap:14px;align-items:center}
  .form-apply{font-size:11px;padding:7px 13px}
  .form-clear{font-size:11px;padding:0}
  .form-error{color:var(--wrong)}
</style>
