<script>
  import ScriptLegend from './ScriptLegend.svelte'
  import ZiLink from './ZiLink.svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { countsLabel } from '../lib/layers.js'
  import { t, formatNumber, localize } from '../lib/i18n.svelte.js'
  import { characterAddress } from '../lib/gallery.js'
  let { card = null, expand = $bindable('none'), onselect = () => {}, onreview = null } = $props()
  const members = $derived(card?.grapheme?.members ?? [{code_point: card?.code_point, char: card?.char}])
  // The 異体字 graph: characters one may be written for this one, and characters related otherwise.
  const variants = $derived(card?.variants ?? { written: [], related: [], sources: {} })
  // Each edge as its source states it: the relation and the sources that give it.
  const relationTitle = v => {
    const by = new Map()
    for (const r of v.relations) by.set(r.relation, new Set([...(by.get(r.relation) ?? []), r.source]))
    return `${v.char} ${v.code_point}\n` + [...by].map(([relation, sources]) => `${relation}: ${[...sources].join(', ')}`).join('\n')
  }
  // The relations that set a character apart from a variant, in the reader's language; another is shown as its source names it.
  const RELATION_NAMES = { borrowed: () => t('chips.relation.borrowed'), substitute: () => t('chips.relation.substitute'),
    'non-cognate': () => t('chips.relation.nonCognate'), spoofing: () => t('chips.relation.spoofing'),
    reduction: () => t('chips.relation.reduction') }
  const relationName = v => { const relation = v.relations[0]?.relation; return RELATION_NAMES[relation]?.() ?? relation }
  const crops = v => (v.count ?? 0) + (v.corpus_count ?? 0)
  // Few fonts reach past the basic plane, and a compatibility ideograph looks like its unified form,
  // so either shows its code point beside it: a missing glyph can still be told apart.
  const named = v => { const p = v.char.codePointAt(0); return p > 0xffff || (p >= 0xf900 && p <= 0xfaff) }
</script>

{#if card}
  <div class="character-layers" aria-label={t('chips.layers.label')}>
    <div class="layer-row">
      <span class="layer-label">{t('chips.grapheme')}</span>
      <button class="family" class:active={expand === 'grapheme'} aria-pressed={expand === 'grapheme'}
        onclick={() => expand = 'grapheme'}>{card.grapheme?.label ?? card.char}</button>
    </div>
    <div class="layer-row">
      <span class="layer-label">{t('chips.characters')}</span>
      <div class="character-members">
        {#each members as member (member.code_point)}
          <div class="member-choice"><button class="member" class:active={expand !== 'grapheme' && member.code_point === card.code_point}
            aria-pressed={expand !== 'grapheme' && member.code_point === card.code_point}
            aria-label={t('chips.showCharacter', { char: member.char })} onclick={() => onselect(member.code_point)}>
            <ReferenceGlyph char={member.char} code_point={member.code_point} script={member.script} size="md" />
          </button><ZiLink character={member.char} compact /></div>
        {/each}
      </div>
    </div>
    {#if variants.written.length}
      <div class="layer-row">
        <span class="layer-label">{t('chips.variants')}</span>
        <div class="variant-chips">
          {#each variants.written as v (v.code_point)}
            <a class="variant" href={localize(characterAddress(v.code_point))} title={relationTitle(v)}><span lang="zh">{v.char}</span>{#if named(v)}<small class="code">{v.code_point}</small>{/if}{#if crops(v)}<small>{formatNumber(crops(v))}</small>{/if}</a>
          {/each}
        </div>
      </div>
    {/if}
    {#if variants.related.length}
      <div class="layer-row">
        <span class="layer-label">{t('chips.related')}</span>
        <div class="variant-chips">
          {#each variants.related as v (v.code_point)}
            <a class="variant" href={localize(characterAddress(v.code_point))} title={relationTitle(v)}><span lang="zh">{v.char}</span>{#if named(v)}<small class="code">{v.code_point}</small>{/if}<small>{relationName(v)}{#if crops(v)} · {formatNumber(crops(v))}{/if}</small></a>
          {/each}
        </div>
      </div>
    {/if}
    {#if variants.written.length || variants.related.length}
      <p class="variant-sources">{t('chips.variantSources')}: {#each Object.entries(variants.sources) as [id, citation], i (id)}{#if i} · {/if}<abbr title={citation}>{id}</abbr>{/each}</p>
    {/if}
    <div class="layer-legend"><ScriptLegend /></div>
    <div class="layer-row forms-row">
      <span class="layer-label">{t('chips.forms')}</span>
      <span>{expand === 'grapheme' ? t('chips.allForms') : card.char}</span>
      {#if expand !== 'grapheme' && card.candidates?.known && countsLabel(card.candidates)}<small>{countsLabel(card.candidates)}</small>{/if}
    </div>
    {#if card.ligature || card.jibo?.length || card.derived?.length || card.expansions?.some(o => o.key !== 'grapheme') || onreview}
      <div class="layer-chips">
        {#if card.ligature}<span class="chip">{card.ligature.components.map(c => c.char).join(' + ')}</span>{/if}
        {#if card.jibo?.length}<span class="chip">字母 {card.jibo.map(j => j.char).join(' ')}</span>{/if}
        {#each (card.expansions ?? []).filter(o => o.key !== 'grapheme') as option (option.key)}
          <button class="chip chip-action" class:active={expand === option.key}
            onclick={() => expand = expand === option.key ? 'none' : option.key}>{option.label} <small>{formatNumber(option.count)}</small></button>
        {/each}
        {#if onreview}<button class="chip chip-action" onclick={() => onreview(card)}>{t('chips.review')}</button>{/if}
      </div>
    {/if}
  </div>
{/if}

<style>
  .character-layers{padding:20px 0;border-bottom:1px solid var(--line);display:grid;gap:12px}
  .layer-row{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
  .layer-label{font-size:11px;color:var(--muted);min-width:76px}
  .layer-legend{padding-left:92px}
  @media(max-width:600px){.layer-legend{padding-left:0}}
  .family{font-size:24px;padding:8px 18px;background:transparent}
  .character-members{display:flex;flex-wrap:wrap;gap:8px;flex:1}
  .member-choice{display:flex;flex-direction:column;align-items:center;gap:4px}
  .member{display:flex;align-items:center;gap:10px;padding:8px 12px;background:transparent}
  .active{color:var(--accent);border-color:var(--accent);background:var(--accent-light)}
  .forms-row{font-size:12px;color:var(--muted)}
  .forms-row small{margin-left:auto}
  .layer-chips{margin:0}
  .variant-chips{display:flex;flex-wrap:wrap;gap:6px;flex:1}
  .variant{display:inline-flex;align-items:baseline;gap:6px;padding:4px 10px;border:1px solid var(--line);border-radius:7px;color:var(--ink);text-decoration:none;font-size:22px;line-height:1.2}
  .variant:hover{border-color:var(--accent)}
  .variant small{font-size:11px;color:var(--muted)}
  .variant .code{font-family:ui-monospace,monospace;font-size:10px}
  .variant-sources{margin:0;padding-left:92px;font-size:11px;color:var(--muted)}
  .variant-sources abbr{text-decoration:underline dotted;cursor:help}
  @media(max-width:600px){.variant-sources{padding-left:0}}
</style>
