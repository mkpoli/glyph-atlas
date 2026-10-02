<script>
  // The bar over a round's selection: how many crops are chosen, the forms of the round's grapheme to
  // mark them as, and the result of the last marking with its undo.
  import ScriptText from './ScriptText.svelte'
  import { t } from '../lib/i18n.svelte.js'
  let { count = 0, forms = [], grapheme = '', busy = false, error = '', done = null,
    onassign, onclear, onundo, ondismiss } = $props()
  const KEYS = '1234567890'
</script>

<div class="form-bar" role="region" aria-label={t('bulk.label')}>
  {#if done}
    <span class="form-done" role="status">{t('bulk.done', { count: done.count, char: done.char })}{#if done.kept} · {t('bulk.kept', { count: done.kept })}{/if}</span>
    <button class="quiet" disabled={busy} onclick={onundo}>{t('bulk.undo')}</button>
    {#if !count}<button class="quiet" onclick={ondismiss} aria-label={t('bulk.dismiss')}>×</button>{/if}
  {/if}
  {#if count}
    <b class="selection-count">{t('bulk.selected', { count })}</b>
    <div class="form-chips" role="group" aria-label={t('quiz.forms.label', { char: grapheme })}>
      {#each forms as form, i (form)}
        <button class="form-chip" disabled={busy} onclick={() => onassign(form)} title={t('bulk.apply', { char: form })} aria-label={t('bulk.apply', { char: form })}>
          <ScriptText text={form} />{#if i < KEYS.length}<kbd>{KEYS[i]}</kbd>{/if}
        </button>
      {/each}
    </div>
    <button class="quiet" disabled={busy} onclick={onclear}>{t('bulk.clear')}</button>
  {/if}
  {#if error}<p class="form-error" role="alert">{error}</p>{/if}
  {#if count}<small class="form-hint">{t('quiz.forms.hint')}</small>{/if}
</div>

<style>
  .form-bar{flex-basis:100%;display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;padding:10px 14px;
    background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 30px var(--shadow-menu);font-size:13px}
  .selection-count{font-weight:600;white-space:nowrap}
  .form-chips{display:flex;flex-wrap:wrap;gap:6px;flex:1;min-width:0}
  .form-chip{display:inline-flex;align-items:baseline;gap:6px;padding:4px 10px;font-size:22px;line-height:1.2;
    font-family:"Kureedo Kata","Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans",serif}
  .form-chip:not(:disabled):hover{border-color:var(--accent);color:var(--accent)}
  .form-chip kbd{font-family:ui-monospace,monospace;font-size:9px;color:var(--muted)}
  .quiet{border:0;background:transparent;color:var(--muted);text-decoration:underline;text-underline-offset:3px;padding:6px 4px;font-size:12px}
  .form-error{flex-basis:100%;margin:0;color:var(--wrong)}
  .form-hint{flex-basis:100%;color:var(--muted);font-size:11px}
  @media(max-width:700px){.form-bar{padding:8px 10px;justify-content:space-between}.form-chips{flex-basis:100%;order:1}.form-chip{font-size:19px;padding:3px 8px}.form-chip kbd,.form-hint{display:none}}
</style>
