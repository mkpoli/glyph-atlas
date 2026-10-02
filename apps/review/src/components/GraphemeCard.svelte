<script>
  // One grapheme and the forms it gathers, with their own code points and counts: 仮 and 假 under one
  // heading. Choosing a form opens that form's own gallery; "All forms" chooses the whole family. The
  // grapheme browser shows it over a pointed tile, and the search box for each grapheme a query names.
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { number } from '../lib/client.js'
  import { t } from '../lib/i18n.svelte.js'

  let { group, onchoose = () => {}, onform = () => {} } = $props()
  const codes = text => [...text].map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
</script>

<div class="grapheme-card">
  <div class="card-head">
    <ReferenceGlyph char={group.char} size="lg" />
    <span><b>{t('chips.grapheme')}</b> <code>{group.key}</code><br />{t('explore.grapheme.forms', { count: group.members.length })} · {number(group.count)}</span>
  </div>
  <ul>
    {#each group.members as member (member.label)}
      <li><button type="button" onclick={() => onform(member.label)}>
        <ReferenceGlyph char={member.label} size="md" /><code>{codes(member.label)}</code><small>{number(member.count)}</small>
      </button></li>
    {/each}
  </ul>
  <button type="button" class="card-all" onclick={() => onchoose(group.key)}>{t('chips.allForms')}</button>
</div>

<style>
  .card-head{display:flex;gap:10px;align-items:center;padding:2px 4px 8px;border-bottom:1px solid var(--line);color:var(--muted);line-height:1.5}
  .card-head b{font-weight:500;color:var(--ink)}
  .card-head>span{font-size:12px}
  ul{list-style:none;margin:6px 0 0;padding:0;max-height:260px;overflow:auto}
  li button{display:flex;flex-direction:row;align-items:center;justify-content:flex-start;gap:10px;width:100%;min-height:0;border:0;border-radius:5px;background:transparent;padding:5px 6px;text-align:left}
  li button:hover,li button:focus-visible{background:var(--accent-light);color:var(--accent)}
  code{font-family:"GenZui Sans",ui-monospace,monospace;font-size:10px;color:var(--muted)}
  li small{margin-left:auto;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .card-all{display:block;min-height:0;width:100%;margin-top:6px;border:0;border-top:1px solid var(--line);border-radius:0;background:transparent;padding:8px 6px 2px;text-align:left;font-size:12px;color:var(--accent)}
  .card-all:hover,.card-all:focus-visible{text-decoration:underline;text-underline-offset:3px}
</style>
