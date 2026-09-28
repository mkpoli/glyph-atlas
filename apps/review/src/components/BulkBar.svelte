<script>
  // The bar under a selection in the collection view: how many tiles are chosen, the character to give
  // them all, and the result of the last correction with its undo.
  import CharacterSearch from './CharacterSearch.svelte'
  import { t } from '../lib/i18n.svelte.js'
  let { count = 0, target = $bindable(''), busy = false, error = '', done = null,
    onapply, onselectall, onclear, onundo, ondismiss } = $props()
  let query = $state('')
</script>

<div class="bulk-bar" role="region" aria-label={t('bulk.label')}>
  {#if count}
    <b class="bulk-count" role="status">{t('bulk.selected', { count })}</b>
    <div class="bulk-assign">
      <CharacterSearch compact bind:value={query} label={t('bulk.character.label')} placeholder={t('bulk.character.placeholder')}
        onselect={item => { target = item.char; query = item.char }} />
      <button class="primary" disabled={!target || busy} onclick={onapply}>{target ? t('bulk.apply', { char: target }) : t('bulk.chooseFirst')}</button>
    </div>
    <button class="quiet" disabled={busy} onclick={onselectall}>{t('bulk.selectAll')}</button>
    <button class="quiet" disabled={busy} onclick={onclear}>{t('bulk.clear')}</button>
  {:else if done}
    <span role="status">{t('bulk.done', { count: done.count, char: done.char })}{#if done.kept} · {t('bulk.kept', { count: done.kept })}{/if}</span>
    <button disabled={busy} onclick={onundo}>{t('bulk.undo')}</button>
    <button class="quiet" onclick={ondismiss} aria-label={t('bulk.dismiss')}>×</button>
  {/if}
  {#if error}<p class="bulk-error" role="alert">{error}</p>{/if}
  {#if count && !done}<small class="bulk-hint">{t('bulk.hint')}</small>{/if}
</div>

<style>
  .bulk-bar{position:sticky;bottom:12px;z-index:6;display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;margin:12px 0;padding:12px 16px;
    background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 30px var(--shadow-menu);font-size:13px}
  .bulk-count{font-weight:600;white-space:nowrap}
  .bulk-assign{display:flex;align-items:center;gap:8px;flex:1 1 320px;min-width:0}
  .bulk-assign :global(.character-search){flex:1;min-width:0}
  .bulk-assign button{white-space:nowrap}
  .quiet{border:0;background:transparent;color:var(--muted);text-decoration:underline;text-underline-offset:3px;padding:6px 4px}
  .bulk-error{flex-basis:100%;margin:0;color:var(--wrong)}
  .bulk-hint{flex-basis:100%;color:var(--muted);font-size:11px}
  @media(max-width:700px){.bulk-bar{bottom:8px;padding:10px 12px}.bulk-assign{flex-basis:100%}}
</style>
