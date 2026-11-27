<script>
  // The bar over a round's selection: how many crops are chosen, the forms of the round's grapheme to
  // mark them as, any other character found by search, a form written as an ideographic description
  // sequence instead, and the result of the last marking with its undo. The grapheme's forms are those
  // the inspector offers a crop of it (`listGrapheme`): its members with their registered figures and
  // regional shapes, its variants and the forms its crops carry; until they are read, and where they
  // cannot be, its members (`forms`).
  import { graphemeLabel } from '$worker/graphemeLabel.ts'
  import FormChips from './FormChips.svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import IdsPick from './IdsPick.svelte'
  import StyleOptions from './StyleOptions.svelte'
  import { CROP_STYLES } from '../lib/cropStyle.js'
  import { listGrapheme } from '../lib/cropForms.js'
  import { t } from '../lib/i18n.svelte.js'
  let { count = 0, forms = [], grapheme = '', busy = false, error = '', done = null, style = $bindable(null),
    onassign, onstyle, onclear, onundo, ondismiss } = $props()
  let query = $state(''), target = $state('')
  let read = $state({ of: '', forms: [] })
  $effect(() => {
    const char = grapheme
    if (!char) return
    let live = true
    listGrapheme(char, [char]).then(found => { if (live) read = { of: char, forms: found.chips ?? [] } }, () => {})
    return () => { live = false }
  })
  const offered = $derived(read.of === grapheme && read.forms.length ? read.forms : forms)
</script>

<div class="form-bar" role="region" aria-label={t('bulk.label')}>
  {#if done}
    <span class="form-done" role="status">{#if done.kind !== 'style'}{t('bulk.done', { count: done.count, char: done.char })}{#if done.kept}{' · '}{t('bulk.kept', { count: done.kept })}{/if}{/if}{#if done.styled?.length}{done.kind !== 'style' ? ' · ' : ''}{t('bulk.styled', { count: done.styled.length, style: t(`style.kind.${done.style}`) })}{/if}</span>
    <button class="quiet" disabled={busy} onclick={onundo}>{t('bulk.undo')}</button>
    {#if !count}<button class="quiet" onclick={ondismiss} aria-label={t('bulk.dismiss')}>×</button>{/if}
  {/if}
  {#if count}
    <b class="selection-count">{t('bulk.selected', { count })}</b>
    <FormChips forms={offered} disabled={busy} label={t('quiz.forms.label', { char: graphemeLabel(grapheme) })} title={char => t('bulk.apply', { char })} onchoose={onassign} />
    <div class="form-assign">
      <CharacterSearch compact bind:value={query} label={t('bulk.character.label')} placeholder={t('bulk.character.placeholder')}
        oninput={() => target = ''} onselect={item => { target = item.char; query = item.char }} />
      <button class="assign-searched" disabled={!target || busy} onclick={() => onassign(target)}>{target ? t('bulk.apply', { char: target }) : t('bulk.chooseFirst')}</button>
    </div>
    <IdsPick char={grapheme} disabled={busy} onuse={onassign} />
    <button class="quiet" disabled={busy} onclick={onclear}>{t('bulk.clear')}</button>
    <div class="form-style" role="group" aria-label={t('style.label')}>
      <span class="form-style-label" aria-hidden="true">{t('style.label')}</span>
      <StyleOptions styles={CROP_STYLES} pressed={style} disabled={busy} onpress={value => style = style === value ? null : value} />
      {#if style}<button disabled={busy} onclick={onstyle}>{t('bulk.applyStyle', { style: t(`style.kind.${style}`) })}</button>{/if}
    </div>
  {/if}
  {#if error}<p class="form-error" role="alert">{error}</p>{/if}
  {#if count}<small class="form-hint">{t('quiz.forms.hint')}</small>{/if}
</div>

<style>
  .form-bar{flex-basis:100%;display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;padding:10px 14px;
    background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 30px var(--shadow-menu);font-size:13px}
  .selection-count{font-weight:600;white-space:nowrap}
  .form-assign{display:flex;align-items:center;gap:8px;flex:1 1 280px;min-width:0}
  .form-assign :global(.character-search){flex:1;min-width:0}
  .form-assign button{white-space:nowrap}
  .form-style{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;flex-basis:100%;min-width:0}
  .form-style-label{flex:none;font-size:12px;color:var(--muted)}
  .form-style button:not(.style-option){white-space:nowrap}
  .quiet{border:0;background:transparent;color:var(--muted);text-decoration:underline;text-underline-offset:3px;padding:6px 4px;font-size:12px}
  .form-error{flex-basis:100%;margin:0;color:var(--wrong)}
  .form-hint{flex-basis:100%;color:var(--muted);font-size:11px}
  @media(max-width:700px){.form-bar{padding:8px 10px;justify-content:space-between}.form-bar :global(.form-chips){flex-basis:100%;order:1}.form-assign{flex-basis:100%;order:2}.form-style{order:3}.form-hint{display:none}}
</style>
