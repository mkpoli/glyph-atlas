<script>
  // The bar under a selection in the collection view: how many tiles are chosen, "Looks right" for all of
  // them, the character to give them all or a form written as an ideographic description sequence (not
  // while a pair or trigram is chosen, `runs`: its crops are different characters), the result of the
  // last batch save with its undo, and the crops it could not save, by reason, each of which opens. On a
  // character's page the forms its card lists (`choices`) are offered as the character to give, unsearched.
  import CharacterSearch from './CharacterSearch.svelte'
  import IdsPick from './IdsPick.svelte'
  import ContributionTerms from './ContributionTerms.svelte'
  import ScriptText from './ScriptText.svelte'
  import Glyph from './Glyph.svelte'
  import FormText from './FormText.svelte'
  import StyleOptions from './StyleOptions.svelte'
  import { CROP_STYLES } from '../lib/cropStyle.js'
  import { isDescription } from '../lib/ids.js'
  import { reportByReason } from '../lib/corrections.js'
  import { t } from '../lib/i18n.svelte.js'
  let { selecting = false, count = 0, runs = 0, choices = [], target = $bindable(''), style = $bindable(null), busy = false, error = '', done = null, report = [],
    onconfirm, onapply, onselectall, onclear, onleave, onundo, onopen, ondismiss } = $props()
  let query = $state('')
  const groups = $derived(reportByReason(report))
  const entries = $derived(new Map(report.map(entry => [entry.id, entry])))
  // Outside the unified ideographs' main block a form may be missing from the reader's fonts or look
  // like another, so its chip names its code point too.
  // What Apply gives the selection: the character or form chosen, the style chosen, or both.
  const applyLabel = $derived(target && style ? t('bulk.applyBoth', { char: target, style: t(`style.kind.${style}`) })
    : target ? t('bulk.apply', { char: target }) : style ? t('bulk.applyStyle', { style: t(`style.kind.${style}`) }) : t('bulk.chooseFirst'))
  const outside = char => { const p = char.codePointAt(0); return p > 0x3000 && !(p >= 0x4e00 && p <= 0x9fff) && !(p >= 0x3040 && p <= 0x30ff) }
</script>

<div class="bulk-bar" role="region" aria-label={t('bulk.label')}>
  {#if selecting}
    <div class="bulk-row">
      <b class="bulk-count" role="status">{t('bulk.selected', { count })}</b>
      <button class="primary bulk-confirm" disabled={!count || busy} onclick={onconfirm} title={t('bulk.looksRight.about')}><span aria-hidden="true">✓</span> {t('bulk.looksRight')}</button>
      <div class="bulk-assign">
        <CharacterSearch compact bind:value={query} label={t('bulk.character.label')} placeholder={t('bulk.character.placeholder')}
          onselect={item => { target = item.char; query = item.char }} />
        <button disabled={!count || (!target && !style) || busy || runs > 0} title={runs ? t('bulk.apply.runs') : undefined} onclick={onapply}>{applyLabel}</button>
      </div>
      <IdsPick char={target} disabled={busy || runs > 0} title={runs ? t('bulk.apply.runs') : undefined} onuse={sequence => { target = sequence; query = sequence }} />
      <span class="bulk-tools">
        <button class="quiet" disabled={busy} onclick={onselectall}>{t('bulk.selectAll')}</button>
        <button class="quiet" disabled={busy || !count} onclick={onclear}>{t('bulk.clear')}</button>
        <button class="bulk-leave" onclick={onleave} aria-keyshortcuts="Escape">{t('bulk.leave')}</button>
      </span>
    </div>
    {#if choices.length}
      <div class="bulk-choices" role="group" aria-label={t('chips.forms')}>
        {#each choices as choice (choice.char)}
          <button class="bulk-choice" class:active={target === choice.char} aria-pressed={target === choice.char} disabled={busy || runs > 0}
            title={runs ? t('bulk.apply.runs') : choice.code_point ?? choice.char} onclick={() => { target = choice.char; query = choice.char }}>
            {#if isDescription(choice.char)}<FormText text={choice.char} />{:else}<ScriptText text={choice.char} script={choice.script} lang="zh" titled={false} />{#if outside(choice.char)}<small class="choice-code">{choice.code_point}</small>{/if}{/if}</button>
        {/each}
      </div>
    {/if}
    <div class="bulk-style" role="group" aria-label={t('style.label')}>
      <span class="bulk-style-label" aria-hidden="true">{t('style.label')}</span>
      <StyleOptions styles={CROP_STYLES} pressed={style} disabled={busy || runs > 0} onpress={value => style = style === value ? null : value} />
    </div>
  {/if}
  {#if done || groups.length}
    <div class="bulk-row bulk-result">
      {#if done}
        <span class="bulk-done" role="status">{#if done.kind !== 'style'}{done.kind === 'confirm' ? t('bulk.confirmed', { count: done.count })
          : t('bulk.done', { count: done.count, char: done.char })}{#if done.kept}{' · '}{t('bulk.kept', { count: done.kept })}{/if}{/if}{#if done.styled?.length}{done.kind !== 'style' ? ' · ' : ''}{t('bulk.styled', { count: done.styled.length, style: t(`style.kind.${done.style}`) })}{/if}</span>
        <button disabled={busy} onclick={onundo}>{t('bulk.undo')}</button>
      {/if}
      {#if !selecting}<button class="quiet" onclick={ondismiss} aria-label={t('bulk.dismiss')}>×</button>{/if}
    </div>
    {#if groups.length}
      <ul class="bulk-report" aria-label={t('bulk.report.label')}>
        {#each groups as [reason, ids] (reason)}
          <li><span class="bulk-reason">{t(`bulk.report.${reason}`, { count: ids.length })}</span>
            {#each ids as id (id)}{@const entry = entries.get(id)}<button class="report-crop" title={id} aria-label={entry.name} onclick={() => onopen(id)}>{#if entry.thumb}<Glyph item={entry.thumb} alt="" class="glyph-image report-thumb" />{/if}{#if entry.named}<ScriptText text={entry.label} titled={false} />{:else}<span class="report-id">{entry.label || id}</span>{/if}</button>{/each}</li>
        {/each}
      </ul>
    {/if}
  {/if}
  {#if error}<p class="bulk-error" role="alert">{error}</p>{/if}
  {#if selecting}<small class="bulk-hint">{t('bulk.hint')}</small><ContributionTerms />{/if}
</div>

<style>
  /* Above the phone's home bar and clear of a notch, where the page reaches under them. */
  .bulk-bar{position:sticky;bottom:max(12px,calc(env(safe-area-inset-bottom) + 8px));z-index:6;display:flex;flex-direction:column;gap:10px;margin:12px 0;
    padding:12px max(16px,env(safe-area-inset-right)) 12px max(16px,env(safe-area-inset-left));
    background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 30px var(--shadow-menu);font-size:13px}
  .bulk-row{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px}
  .bulk-count{font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums;min-width:7ch}
  .bulk-confirm{white-space:nowrap;min-width:0;gap:8px;justify-content:center;padding:10px 18px}
  .bulk-assign{display:flex;align-items:center;gap:8px;flex:1 1 300px;min-width:0}
  .bulk-assign :global(.character-search){flex:1;min-width:0}
  .bulk-assign button{white-space:nowrap}
  .bulk-tools{display:flex;align-items:center;gap:6px;margin-left:auto}
  .bulk-leave{padding:8px 12px}
  .bulk-style{display:flex;align-items:center;gap:12px;flex-basis:100%;min-width:0}
  .bulk-style-label{flex:none;font-size:12px;color:var(--muted)}
  .bulk-choices{display:flex;flex-wrap:wrap;gap:6px}
  .bulk-choice{min-width:38px;min-height:38px;padding:3px 8px;font-size:20px;line-height:1.2;justify-content:center;gap:4px;border-radius:6px}
  .choice-code{font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}
  .bulk-choice.active{color:var(--accent);border-color:var(--accent);background:var(--accent-light)}
  .quiet{border:0;background:transparent;color:var(--muted);text-decoration:underline;text-underline-offset:3px;padding:6px 4px}
  .bulk-result{gap:8px 12px}
  .bulk-report{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px;max-height:9.5em;overflow:auto}
  .bulk-report li{display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px}
  .bulk-reason{color:var(--muted);margin-right:4px}
  .report-crop{display:inline-flex;align-items:center;gap:6px;min-width:32px;min-height:34px;padding:3px 7px;font-size:16px;line-height:1.2;border-radius:5px}
  .report-crop :global(.report-thumb){display:block;width:26px;height:26px}
  .report-id{font-size:11px;color:var(--muted);max-width:14ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .bulk-error{margin:0;color:var(--wrong)}
  .bulk-hint{color:var(--muted);font-size:11px}
  @media(max-width:700px){.bulk-bar{bottom:max(8px,calc(env(safe-area-inset-bottom) + 4px));padding-top:10px;padding-bottom:10px}
    .bulk-assign{flex-basis:100%}.bulk-tools{margin-left:0}.bulk-hint{display:none}}
</style>
