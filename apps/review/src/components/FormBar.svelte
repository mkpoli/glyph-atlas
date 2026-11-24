<script>
  // The bar over a round's selection: how many crops are chosen, the forms of the round's grapheme to
  // mark them as, any other character found by search, a form written as an ideographic description
  // sequence instead, and the result of the last marking with its undo.
  import { graphemeLabel } from '$worker/graphemeLabel.ts'
  import FormChips from './FormChips.svelte'
  import CharacterSearch from './CharacterSearch.svelte'
  import IdsPick from './IdsPick.svelte'
  import { t } from '../lib/i18n.svelte.js'
  let { count = 0, forms = [], grapheme = '', busy = false, error = '', done = null,
    onassign, onclear, onundo, ondismiss } = $props()
  let query = $state(''), target = $state('')
</script>

<div class="form-bar" role="region" aria-label={t('bulk.label')}>
  {#if done}
    <span class="form-done" role="status">{t('bulk.done', { count: done.count, char: done.char })}{#if done.kept}{' · '}{t('bulk.kept', { count: done.kept })}{/if}</span>
    <button class="quiet" disabled={busy} onclick={onundo}>{t('bulk.undo')}</button>
    {#if !count}<button class="quiet" onclick={ondismiss} aria-label={t('bulk.dismiss')}>×</button>{/if}
  {/if}
  {#if count}
    <b class="selection-count">{t('bulk.selected', { count })}</b>
    <FormChips {forms} disabled={busy} label={t('quiz.forms.label', { char: graphemeLabel(grapheme) })} title={char => t('bulk.apply', { char })} onchoose={onassign} />
    <div class="form-assign">
      <CharacterSearch compact bind:value={query} label={t('bulk.character.label')} placeholder={t('bulk.character.placeholder')}
        oninput={() => target = ''} onselect={item => { target = item.char; query = item.char }} />
      <button class="assign-searched" disabled={!target || busy} onclick={() => onassign(target)}>{target ? t('bulk.apply', { char: target }) : t('bulk.chooseFirst')}</button>
    </div>
    <IdsPick char={grapheme} disabled={busy} onuse={onassign} />
    <button class="quiet" disabled={busy} onclick={onclear}>{t('bulk.clear')}</button>
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
  .quiet{border:0;background:transparent;color:var(--muted);text-decoration:underline;text-underline-offset:3px;padding:6px 4px;font-size:12px}
  .form-error{flex-basis:100%;margin:0;color:var(--wrong)}
  .form-hint{flex-basis:100%;color:var(--muted);font-size:11px}
  @media(max-width:700px){.form-bar{padding:8px 10px;justify-content:space-between}.form-bar :global(.form-chips){flex-basis:100%;order:1}.form-assign{flex-basis:100%;order:2}.form-hint{display:none}}
</style>
