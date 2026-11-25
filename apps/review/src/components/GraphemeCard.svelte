<script>
  // One grapheme and the forms it gathers, with their own code points and counts: 仮 and 假 under one
  // heading. The heading links to the grapheme's page (the collection narrowed to it when it has none);
  // choosing a form opens that form's own gallery. The grapheme browser shows it over a pointed tile, and
  // the search box for each grapheme a query names. A count of null is not known, and is not shown;
  // `forms` is how many forms the grapheme has, where `members` are only some; `more` are the rest, offered
  // under the list.
  import { tick } from 'svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { labelParts } from '$worker/graphemeLabel.ts'
  import { number } from '../lib/client.js'
  import { t, localize } from '../lib/i18n.svelte.js'
  import { characterAddress, graphemeAddress } from '../lib/addresses.js'

  let { group, onform = () => {} } = $props()
  const parts = $derived(group.alone ? { text: group.char, simplified: '' } : labelParts(group.char))
  let widened = $state(false), list = $state(null)
  const listed = $derived(widened ? [...group.members, ...group.more ?? []] : group.members)
  // The first form shown takes the focus the button had.
  async function widen() {
    widened = true
    await tick()
    list?.querySelectorAll('button')[group.members.length]?.focus()
  }
  const codes = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
</script>

<div class="grapheme-card">
  <a class="card-head" href={localize(group.alone ? characterAddress(group.key) : graphemeAddress(group.key, group.char))}>
    <ReferenceGlyph char={parts.text} simplified={parts.simplified} size="lg" />
    <span><b>{t(group.alone ? 'chips.forms' : 'chips.grapheme')}</b> <code>{group.key}</code><br />{t('explore.grapheme.forms', { count: group.forms ?? group.members.length })}{group.count === null ? '' : ` · ${number(group.count)}`}</span>
  </a>
  <ul bind:this={list}>
    {#each listed as member (member.label)}
      <li><button type="button" onclick={() => onform(member.label)}>
        <ReferenceGlyph char={member.label} size="md" /><code>{codes(member.label)}</code>{#if member.count !== null}<small>{number(member.count)}</small>{/if}
      </button></li>
    {/each}
  </ul>
  {#if group.more?.length && !widened}<button type="button" class="card-more" onclick={widen}>{t('forms.showMore', { count: group.more.length })}</button>{/if}
</div>

<style>
  .card-head{display:flex;gap:10px;align-items:center;padding:2px 4px 8px;border-bottom:1px solid var(--line);color:var(--muted);line-height:1.5;text-decoration:none}
  .card-head:hover b,.card-head:focus-visible b{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
  .card-head b{font-weight:500;color:var(--ink)}
  .card-head>span{font-size:12px}
  ul{list-style:none;margin:6px 0 0;padding:0;max-height:260px;overflow:auto}
  li button{display:flex;flex-direction:row;align-items:center;justify-content:flex-start;gap:10px;width:100%;min-height:0;border:0;border-radius:5px;background:transparent;padding:5px 6px;text-align:left}
  li button:hover,li button:focus-visible{background:var(--accent-light);color:var(--accent)}
  code{font-family:"GenZui Sans",ui-monospace,monospace;font-size:10px;color:var(--muted)}
  .card-more{display:block;width:100%;margin-top:4px;padding:6px;border:0;border-radius:5px;background:transparent;color:var(--accent);font-size:12px;text-align:start}
  .card-more:hover,.card-more:focus-visible{background:var(--accent-light)}
  li small{margin-left:auto;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
</style>
