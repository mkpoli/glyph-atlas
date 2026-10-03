<script>
  // A crop's book's dates where its source is named: the copy's date and its text's, each written as
  // the site reads it, with the source's own words on hover. A Japanese date links to the HuTime
  // request that converted it, so a reader can check the conversion; an undated book says so.
  import { t } from '../lib/i18n.svelte.js'
  import { datingTitle, eventLabel } from '../lib/dating.js'
  let { item } = $props()
  const witness = $derived(item?.dating?.witness ?? null), composed = $derived(item?.dating?.composed ?? null)
  const title = $derived(datingTitle(item))
</script>

{#snippet event(kind, date)}{#if date.hutime}<a href={date.hutime} target="_blank" rel="noreferrer" {title} aria-label={`${eventLabel(kind, date)}. ${title}`}>{eventLabel(kind, date)}</a>{:else}<span tabindex="0" {title} aria-label={`${eventLabel(kind, date)}. ${title}`}>{eventLabel(kind, date)}</span>{/if}{/snippet}
{#if item?.dating}<span class="date-mark">{#if witness}{@render event(witness.kind, witness)}{:else}<span class="undated">{t('date.undated')}</span>{/if}{#if composed}{'\u00a0· '}{@render event('composed', composed)}{/if}{#if witness?.status === 'disputed' || composed?.status === 'disputed'}<span class="disputed" role="img" aria-label={t('date.disputed')} title={t('date.disputed')}>*</span>{/if}</span>{/if}

<style>
  .date-mark{font-variant-numeric:tabular-nums}
  .date-mark a{color:inherit;text-decoration:underline dotted;text-underline-offset:2px}
  .date-mark a:hover{color:var(--muted)}
  .undated{font-style:italic}
  .disputed{margin-left:1px;color:var(--wrong)}
</style>
