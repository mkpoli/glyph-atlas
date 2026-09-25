<script>
  import ScriptLegend from './ScriptLegend.svelte'
  import ZiLink from './ZiLink.svelte'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import { countsLabel } from '../lib/layers.js'
  import { t } from '../lib/i18n.svelte.js'
  let { card = null, expand = $bindable('none'), onselect = () => {}, onreview = null } = $props()
  const members = $derived(card?.grapheme?.members ?? [{code_point: card?.code_point, char: card?.char}])
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
            onclick={() => expand = expand === option.key ? 'none' : option.key}>{option.label} <small>{option.count}</small></button>
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
  .active{color:var(--accent);border-color:var(--accent);background:var(--accent-light,#f0edff)}
  .forms-row{font-size:12px;color:var(--muted)}
  .forms-row small{margin-left:auto}
  .layer-chips{margin:0}
</style>
