<script>
  // Where a crop's image comes from and on what terms, in one quiet line: the work, its holder and
  // licence, when its copy was made, who gave the label, and the record that states the rights.
  import { t } from '../lib/i18n.svelte.js'
  import { licenceName, holderName } from '../lib/licence.js'
  import DateMark from './DateMark.svelte'
  // `omit` is a link the view offers on its own, which the line then leaves out.
  let { item, corpus = false, omit = null } = $props()
  // A no-break space keeps each · with the part before it.
  const SEPARATOR = '\u00a0· '
  const shown = $derived((corpus
    ? [item.source?.title, holderName(item.attribution || item.source?.holder), licenceName(item.licence)]
    : [item.source, holderName(item.attribution || item.holder), licenceName(item.licence)]).filter(Boolean))
  const credit = $derived(corpus && item.text_attribution ? t('corpus.labelCredit', { credit: item.text_attribution }) : null)
  const record = $derived(corpus
    ? (/^https?:\/\//i.test(item.record_url ?? '') ? { href: item.record_url, label: t('corpus.sourceRecord') } : null)
    : (item.rights_url ? { href: item.rights_url, label: t('character.sourceRights') } : null))
  const honkoku = $derived(/^https:\/\/app\.honkoku\.org\//.test(item.honkoku_url ?? '') ? { href: item.honkoku_url, label: t('character.sourceHonkoku') } : null)
  const links = $derived([record, honkoku].filter(link => link && link.href !== omit))
</script>

{#if shown.length || item.dating || credit || links.length}
  <!-- Each part stays whole and the line breaks only after a separator; a part longer than the line wraps inside itself. -->
  <p class="source-credit">{#each shown as part, i}{#if i}{SEPARATOR}{/if}<span class="part">{part}</span>{/each}{#if item.dating}{#if shown.length}{SEPARATOR}{/if}<DateMark {item} />{/if}{#if credit}{#if shown.length || item.dating}{SEPARATOR}{/if}<span class="part">{credit}</span>{/if}{#each links as link, i}{#if i || shown.length || item.dating || credit}{SEPARATOR}{/if}<a class="part" href={link.href} target="_blank" rel="noreferrer">{link.label}</a>{/each}</p>
{/if}

<style>
  .source-credit{margin:0;font-size:11px;line-height:1.5;color:var(--faint);overflow-wrap:anywhere}
  .source-credit a{color:inherit;text-decoration:underline;text-underline-offset:2px}
  .source-credit a:hover{color:var(--muted)}
  .part{display:inline-block;max-width:100%;vertical-align:top}
  .source-credit :global(.date-mark > a),.source-credit :global(.date-mark > span:not(.disputed)){white-space:nowrap}
</style>
