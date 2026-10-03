<script>
  // Where a crop's image comes from and on what terms, in one quiet line: the work, its holder and
  // licence, who gave the label, and the record that states the rights.
  import { t } from '../lib/i18n.svelte.js'
  import { licenceName, holderName } from '../lib/licence.js'
  let { item, corpus = false } = $props()
  const parts = $derived(corpus
    ? [item.source?.title, holderName(item.attribution || item.source?.holder), licenceName(item.licence),
       item.text_attribution ? t('corpus.labelCredit', { credit: item.text_attribution }) : null]
    : [item.source, holderName(item.attribution || item.holder), licenceName(item.licence)])
  const link = $derived(corpus
    ? (/^https?:\/\//i.test(item.record_url ?? '') ? { href: item.record_url, label: t('corpus.sourceRecord') } : null)
    : (item.rights_url ? { href: item.rights_url, label: t('character.sourceRights') } : null))
</script>

{#if parts.some(Boolean)}
  <p class="source-credit">{parts.filter(Boolean).join(' · ')}{#if link}{' · '}<a href={link.href} target="_blank" rel="noreferrer">{link.label}</a>{/if}</p>
{/if}

<style>
  .source-credit{margin:0;font-size:11px;line-height:1.5;color:var(--faint);overflow-wrap:anywhere}
  .source-credit a{color:inherit;text-decoration:underline;text-underline-offset:2px}
  .source-credit a:hover{color:var(--muted)}
</style>
