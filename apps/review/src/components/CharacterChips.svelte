<script>
  import ScriptLegend from './ScriptLegend.svelte'
  import { chronologyAddress } from '../lib/chronology.js'
  import ZiLink from './ZiLink.svelte'
  import Cite from './Cite.svelte'
  import { characterEntry } from '$worker/citation.ts'
  import { labelParts } from '$worker/graphemeLabel.ts'
  import ReferenceGlyph from './ReferenceGlyph.svelte'
  import ScriptLine from './ScriptLine.svelte'
  import ScriptText from './ScriptText.svelte'
  import FormText from './FormText.svelte'
  import IdsLabel from './IdsLabel.svelte'
  import OriginText from './OriginText.svelte'
  import StructureDialog from './StructureDialog.svelte'
  import { matchesLabel } from '../lib/layers.js'
  import { isDescription } from '../lib/ids.js'
  import { idsCard } from '../lib/idsCard.js'
  import { isFigure } from '../lib/mjFigures.js'
  import { cardStructures, layouts, proposed, standing } from '../lib/structures.js'
  import { signedInUser } from '../lib/session.svelte.js'
  import { page } from '$app/state'
  import { t, withText, formatNumber, localize } from '../lib/i18n.svelte.js'
  import { characterAddress, graphemeAddress } from '../lib/addresses.js'
  import { roundAddress } from '../lib/reviewRounds.js'
  import { runAddress } from '../lib/ngrams.js'
  import { originTitle } from '../lib/origin.js'
  import { familyCodeSet } from '../lib/forms.js'
  import { cardTiers, inferred, isSeal, structureForms } from '../lib/cardTiers.js'
  // `narrowed` is the style group, order and years the gallery is shown with, which the pages of the
  // grapheme and of its forms keep.
  let { card = null, expand = $bindable('none'), narrowed = {}, onreview = null } = $props()
  // The page is the grapheme's or the one character's, and the chip of it is marked rather than linked.
  const whole = $derived(expand === 'grapheme')
  const { members, seals, variants, grapheme, writers, sharedWriters, writes, selfGrapheme, shared, written, related, described } = $derived(cardTiers(card))
  // What the gallery shows, cited: the grapheme, or the one form. A widening to variants has no address of
  // its own to cite.
  const entry = $derived(card?.code_point && expand !== 'variants' ? characterEntry(whole ? 'grapheme' : 'form', card) : null)
  // The Forms family this character is clustered in, by its grapheme or its own code point; none, no link.
  let clustered = $state(null)
  $effect(() => { if (page.data.forms) familyCodeSet().then(codes => { clustered = codes }) })
  const formsFamily = $derived([card?.grapheme?.code_point, card?.code_point].find(code => code && clustered?.has(code)))
  // Every relation of kanji-variants.tsv in the reader's language; one the table gains later reads as its id.
  const RELATION_NAMES = {
    variant: () => t('chips.relation.variant'), equivalent: () => t('chips.relation.equivalent'),
    overlap: () => t('chips.relation.overlap'), semantic: () => t('chips.relation.semantic'),
    'specialized-semantic': () => t('chips.relation.specializedSemantic'), z: () => t('chips.relation.z'),
    simplified: () => t('chips.relation.simplified'), shinjitai: () => t('chips.relation.shinjitai'),
    regional: () => t('chips.relation.regional'), shuowen: () => t('chips.relation.shuowen'),
    reduction: () => t('chips.relation.reduction'), compatibility: () => t('chips.relation.compatibility'),
    borrowed: () => t('chips.relation.borrowed'), substitute: () => t('chips.relation.substitute'),
    'non-cognate': () => t('chips.relation.nonCognate'), spoofing: () => t('chips.relation.spoofing'),
  }
  // A small seal pair reads from the card's side: the seal character on a Han card, and on a seal
  // card the Han character Unicode gives as its modern equivalent.
  const relationName = (relation, v) => relation === 'small-seal'
    ? t(isSeal(v?.char) ? 'chips.relation.smallSeal' : 'chips.relation.sealEquivalent')
    : RELATION_NAMES[relation]?.() ?? relation
  // A pair's relations, each once, with the sources that state it.
  const relationsOf = v => {
    const by = new Map()
    for (const r of v.relations) by.set(r.relation, new Set([...(by.get(r.relation) ?? []), r.source]))
    return [...by].map(([relation, sources]) => ({ relation, name: relationName(relation, v), sources: [...sources] }))
  }
  const relationTitle = v => [`${v.char} ${v.code_point}`, ...(sharedWriters.has(v.code_point) ? [shareLabel(v)] : []),
    ...relationsOf(v).map(r => `${r.name}: ${r.sources.join(', ')}`),
    ...(inferred(v) ? [t('chips.derived'), ...v.relations.map(r => r.detail)] : [])].join('\n')
  // A chip names one relation, the most specific a pair has; its title lists them all with their sources.
  const RELATION_RANK = ['simplified', 'shinjitai', 'regional', 'shuowen', 'reduction', 'compatibility', 'small-seal', 'z',
    'variant', 'equivalent', 'specialized-semantic', 'overlap', 'semantic', 'borrowed', 'substitute', 'non-cognate', 'spoofing']
  const rank = relation => (RELATION_RANK.indexOf(relation) + RELATION_RANK.length + 1) % (RELATION_RANK.length + 1)
  const mainRelation = v => relationsOf(v).sort((a, b) => rank(a.relation) - rank(b.relation))[0]?.name ?? ''
  // A derived chip's evidence, after the word that marks it: each route it came by, each substitution
  // as made with its count of contexts and the pairs behind it with their sources.
  const derivedTitle = v => `${v.char} · ${t('chips.derived')}\n` + (v.routes ?? []).map(route => route.map(s =>
    `${s.was} → ${s.became} · ${s.count}\n` + s.pairs.map(p => `${p.a} ${p.b} ${p.sources.join(', ')}`).join('\n')).join('\n+ ')).join('\n\n')
  // A small seal character of the grapheme names the Han character that makes it one and the source.
  const sealTitle = v => [`${v.char} ${v.code_point}`, ...(grapheme.evidence ?? []).flatMap(source => (source.edges ?? [])
    .filter(edge => edge.role === 'seal' && edge.b === v.char).map(edge => `${t('chips.relation.smallSeal')}: ${edge.a} · ${source.id}`)),
    ...(glyphCount(v) ? [glyphCount(v)] : [])].join('\n')
  const crops = v => (v.count ?? 0) + (v.corpus_count ?? 0)
  // What a chip's number counts: the crops filed under it here and the corpus glyphs recorded for it.
  const glyphCount = v => crops(v) ? t('explore.meta.glyphs', { count: crops(v) }) : undefined
  const shareLabel = v => writers.has(v.code_point) ? t('chips.alsoOther') : t('chips.alsoWritten', { char: card.char })
  // The 構形 of the grapheme's 字体, each with the characters and described forms that share it (峰 and
  // 峯 are two, 說 and 説 one, and ⿰月兊 sits with 脫), its small seal characters last in theirs and
  // marked as proposed (𾲟 with 臭). A name with no structure, and a site that knows none yet, shows no row.
  const HAN = /^\p{Script=Han}$/u
  const composable = list => [...new Set(list.map(v => v.char)
    .filter(char => HAN.test(char ?? '') || isSeal(char) || isDescription(char) || isFigure(char ?? '')))]
  const forms = $derived(structureForms({ members, written, described, seals, related }))
  const composedOf = $derived(composable(forms).slice(0, 48).join(''))
  // The forms shown when arranging 構形: the grapheme's 字体 and its related characters.
  const arrangeOf = $derived(composable([...forms, ...related]))
  const composedNames = $derived(composed.reduce((n, group) => n + group.chars.length, 0))
  // The last answer stays until the next one arrives, so the rows below do not jump while it loads.
  // A signed-in reader opens the 構形 to arrange them, and the row is read again once they have.
  let composed = $state([]), twins = $state([]), arranging = $state(false), arrangedTimes = $state(0)
  // The derived forms are drawn once the list is open, since each drawing is a request.
  let derivedOpen = $state(false)
  const cardChar = $derived(card?.char)
  $effect(() => {
    const chars = composedOf, char = cardChar
    void arrangedTimes
    if (!chars) { composed = []; twins = []; return }
    let live = true
    cardStructures(char, chars).then(found => { if (live) { composed = found.items; twins = found.twins } }, () => { if (live) { composed = []; twins = [] } })
    return () => { live = false }
  })
  // The words this character is cited as writing (decision 0004), each with the other spellings cited
  // for it: characters that write the same word, kept out of the variant tiers and the widening. A card
  // cached before the word tables existed has no `words`; it reads as none.
  const words = $derived({ items: [], sources: {}, ...card?.words })
  const wordCrops = w => w.spellings.find(s => s.current)?.sources.find(s => s.source === 'atlas-ledger')
  // One line per source: a 振り仮名 row names the reading it counts, since 抔 is counted under など and なと.
  const spellingTitle = s => s.spelling + '\n' + s.sources.map(c => [c.ruby ? `${c.source} ${c.ruby}` : c.source,
    c.documents ? t('chips.documents', { count: c.documents }) : ''].filter(Boolean).join(' · ')).join('\n')
  // Outside the unified ideographs' main block a character may be missing from the reader's fonts or
  // look like another (a compatibility ideograph, a Kangxi radical), so it shows its code point too.
  const named = v => { const p = v.char.codePointAt(0); return !(p >= 0x4e00 && p <= 0x9fff) }
</script>

{#snippet sharing(names, group)}
  {#if names.length}<span class="sharing">{#each names as name (name)}{#if composedNames > 1}{#if isDescription(name)}<FormText text={name} />{:else}<ScriptText text={name} lang="zh" titled={false} />{/if}{/if}{#if proposed(name, group.claimed?.[name])}<sup class="derived-mark" title="{name} · {t('chips.derived')}">*</sup>{/if}{/each}</span>{/if}
{/snippet}

{#snippet form(member)}
  <ReferenceGlyph char={member.char} code_point={member.code_point} script={member.script} size="md" showCodePoint={isSeal(member.char) || twins.includes(member.char)} />
  {#if crops(member)}<small class="use">{formatNumber(crops(member))}</small>{/if}
{/snippet}

{#snippet head()}<ScriptText {...labelParts(grapheme.char)} titled={false} />{#if crops(grapheme)}<small>{formatNumber(crops(grapheme))}</small>{/if}{/snippet}

{#snippet choice(member)}
  {@const title = isSeal(member.char) ? sealTitle(member) : glyphCount(member)}
  <div class="member-choice">{#if !whole && member.code_point === card.code_point}<span class="member" aria-current="page" {title}>{@render form(member)}</span><ZiLink character={member.char} compact />
    {:else}<a class="member" href={localize(characterAddress(member.code_point, narrowed))} {title}
      aria-label={crops(member) ? `${t('chips.showCharacter', { char: member.char })} · ${formatNumber(crops(member))}`
        : t('chips.showCharacter', { char: member.char })}>{@render form(member)}</a>{/if}</div>
{/snippet}

{#if card}
  <div class="character-layers" aria-label={t('chips.layers.label')}>
    <div class="layer-row">
      <span class="layer-label">{t('chips.grapheme')}</span>
      <div class="variant-chips">
        {#if selfGrapheme || whole}
          <div class="member-choice">{#if whole}<span class="variant" aria-current="page" title={glyphCount(grapheme)}>{@render head()}</span><ZiLink character={grapheme.char} compact />
            {:else}<a class="variant" href={localize(graphemeAddress(grapheme.code_point, grapheme.char, narrowed))} title={glyphCount(grapheme)}>{@render head()}</a>{/if}</div>
        {/if}
        {#each writes as v (v.code_point)}
          <a class="variant" href={localize(graphemeAddress(v.code_point, v.char))} title={glyphCount(v) ? `${shareLabel(v)}\n${glyphCount(v)}` : shareLabel(v)}><ScriptText {...labelParts(v.char)} titled={false} />{#if shared}<small class="share">{t('chips.shared')}</small>{/if}{#if crops(v)}<small>{formatNumber(crops(v))}</small>{/if}</a>
        {/each}
      </div>
    </div>
    {#if composed.length}
      <div class="layer-row">
        <span class="layer-label">{t('chips.structure')}</span>
        <div class="variant-chips">
          {#each composed as s (s.id)}
            {@const { own, folded } = layouts(s)}
            <span class="variant structure"><IdsLabel text={s.label} />{@render sharing(own, s)}{#each folded as part (part.id)}<span class="layout"><IdsLabel text={part.label} />{@render sharing(part.chars, s)}</span>{/each}</span>
          {/each}
          {#if signedInUser()}<button type="button" class="arrange" aria-haspopup="dialog" onclick={() => arranging = true}>{t('structure.arrange')}</button>{/if}
        </div>
      </div>
      {#if arranging}
        <StructureDialog of={card.char} forms={arrangeOf.map(char => ({ char }))} read={standing} current={card.char} close={() => arranging = false} changed={() => arrangedTimes++} />
      {/if}
    {/if}
    <div class="layer-row">
      <span class="layer-label">{t('chips.forms')}</span>
      <div class="character-members">
        {#each members as member (member.code_point)}{@render choice(member)}{/each}
        {#each written as v (v.code_point)}
          <a class="variant" href={localize(characterAddress(v.code_point))} title={relationTitle(v)}><ScriptText text={v.char} script={v.script} lang="zh" titled={false} />{#if named(v)}<small class="code">{v.code_point}</small>{/if}{#if sharedWriters.has(v.code_point)}<small class="share">{t('chips.shared')}</small>{/if}<small>{#if !v.widens}{mainRelation(v)}{#if crops(v)}{' · '}{/if}{/if}{#if crops(v)}{formatNumber(crops(v))}{/if}</small></a>
        {/each}
        {#each described as v (v.form ?? v.char)}
          {#if v.form}
            <a class="variant" href={localize('/form/' + encodeURIComponent(v.form))} title={crops(v) ? t('explore.meta.glyphs', { count: crops(v) }) : v.char}><FormText text={v.char} />{#if crops(v)}<small>{formatNumber(crops(v))}</small>{/if}</a>
          {:else}
            <span class="variant unencoded" title={crops(v) ? t('explore.meta.glyphs', { count: crops(v) }) : v.char}><FormText text={v.char} />{#if crops(v)}<small>{formatNumber(crops(v))}</small>{/if}</span>
          {/if}
        {/each}
        {#each seals as member (member.code_point)}{@render choice(member)}{/each}
        {#if variants.derived.length}
          <details class="derived" bind:open={derivedOpen}>
            <summary>{t('chips.derived')} <small>{formatNumber(variants.derived.length)}</small></summary>
            {#if derivedOpen}<div class="variant-chips">
              {#each variants.derived as v (v.code_point ?? v.char)}
                {#if v.code_point}
                  <a class="variant" href={localize(characterAddress(v.code_point))} title={derivedTitle(v)}><ScriptText text={v.char} script={v.script} lang="zh" titled={false} /><sup class="derived-mark" aria-hidden="true">*</sup>{#if named(v)}<small class="code">{v.code_point}</small>{/if}{#if crops(v)}<small>{formatNumber(crops(v))}</small>{/if}</a>
                {:else}
                  <span class="variant unencoded" title={derivedTitle(v)}><span class="derived-form" title="" {@attach idsCard(v.char, { lang: 'zh' })}><ScriptText text={v.char} script={v.script} lang="zh" titled={false} /></span><sup class="derived-mark" aria-hidden="true">*</sup></span>
                {/if}
              {/each}
            </div>{/if}
          </details>
        {/if}
      </div>
    </div>
    {#if related.length}
      <div class="layer-row">
        <span class="layer-label">{t('chips.related')}</span>
        <div class="variant-chips">
          {#each related as v (v.code_point)}
            <a class="variant" href={localize(characterAddress(v.code_point))} title={relationTitle(v)}><ScriptText text={v.char} script={v.script} lang="zh" titled={false} />{#if inferred(v)}<sup class="derived-mark" aria-hidden="true">*</sup>{/if}{#if named(v)}<small class="code">{v.code_point}</small>{/if}<small>{mainRelation(v)}{#if crops(v)}{' · '}{formatNumber(crops(v))}{/if}</small></a>
          {/each}
        </div>
      </div>
    {/if}
    {#if variants.items.length || variants.related.length || variants.derived.length}
      {#if variants.items.length && !whole}<button class="chip chip-action include-variants" class:active={expand === 'variants'} aria-pressed={expand === 'variants'}
        onclick={() => expand = expand === 'variants' ? 'none' : 'variants'}>{t('chips.includeVariants')}{#if variants.items.some(crops)} <small>{formatNumber(variants.items.reduce((n, v) => n + crops(v), 0))}</small>{/if}</button>{/if}
      <details class="variant-sources">
        <summary>{t('chips.variantSources')}: {Object.keys(variants.sources).join(' · ')}</summary>
        <ul>{#each Object.entries(variants.sources) as [id, citation] (id)}<li><b>{id}</b> {citation}</li>{/each}</ul>
      </details>
    {/if}
    {#each words.items as w, i (w.id)}
      {@const crops = wordCrops(w)}
      <div class="layer-row">
        <span class="layer-label">{#if !i}{t('chips.words')}{/if}</span>
        <div class="variant-chips">
          <span class="word-reading" title={w.class}><ScriptText text={w.reading} titled={false} /></span>
          {#if crops}
            <span class="word-crops" title={t('chips.documents', { count: crops.documents })}>{t('chips.wordCrops', { count: crops.occurrences })}</span>
          {/if}
          {#each w.spellings.filter(s => !s.current) as s (s.spelling)}
            <a class="variant" href={localize(s.code_point ? characterAddress(s.code_point) : runAddress(s.spelling))} title={spellingTitle(s)}><ScriptText text={s.spelling} titled={false} /></a>
          {/each}
        </div>
      </div>
    {/each}
    {#if words.items.length}
      <details class="variant-sources">
        <summary>{t('chips.variantSources')}: {Object.keys(words.sources).join(' · ')}</summary>
        <ul>{#each Object.entries(words.sources) as [id, citation] (id)}<li><b>{id}</b> {citation}</li>{/each}</ul>
      </details>
    {/if}
    <div class="layer-legend"><ScriptLegend /></div>
    <div class="layer-row forms-row">
      <span class="layer-label">{t('chips.glyphs')}</span>
      <span>{#if whole}<ScriptLine line={{ ...withText('cite.label.grapheme', 'char', { char: card.char, codePoint: card.code_point }), ...labelParts(card.char) }} />{:else}<ScriptText text={card.char} script={card.script} />{/if}</span>
      {#if formsFamily}<a class="forms-link" href={localize('/forms/' + formsFamily)}>{t('nav.forms')} →</a>{/if}
      {#if card.code_point}<a class="forms-link" href={localize(chronologyAddress(card.code_point, { scope: whole ? 'grapheme' : '' }))}>{t('nav.chronology')} →</a>{/if}
      {#if grapheme?.code_point}<a class="forms-link" href={localize(roundAddress(grapheme.code_point))}>{t('nav.quickReview')} →</a>{/if}
      {#if entry}<Cite {entry} class="cite-link" />{/if}
      {#if !whole && card.candidates?.known && matchesLabel(card.candidates)}<small>{matchesLabel(card.candidates)}</small>{/if}
    </div>
    <!-- What a character is made from is the character's own, so a grapheme's page leaves it to its forms'. -->
    {#if !whole && (card.ligature || card.jibo?.length || card.origin?.length || card.derived?.length || card.expansions?.some(o => o.key !== 'grapheme') || onreview)}
      <div class="layer-chips">
        {#if card.ligature}<span class="chip">{#each card.ligature.components as c, i (i)}{#if i}{' + '}{/if}<ScriptText text={c.char} script={c.script} />{/each}</span>{/if}
        {#if card.jibo?.length}<span class="chip">字母{#each card.jibo as j, i (i)}{' '}<ScriptText text={j.char} />{/each}</span>{/if}
        {#if card.origin?.length}<span class="chip chip-origin" title={originTitle(card.origin)}>{t('origin.label')} <OriginText origin={card.origin} titled={false} /></span>{/if}
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
  /* A label sits on the first line of chips, however many lines a row wraps to. */
  .layer-row{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap}
  .layer-label{font-size:11px;color:var(--muted);min-width:76px;line-height:43px}
  .layer-legend{padding-left:92px}
  @media(max-width:600px){.layer-legend{padding-left:0}}
  .character-members{display:flex;flex-wrap:wrap;align-items:flex-start;gap:8px;flex:1}
  .variant-chips .variant, .character-members > .variant{min-height:43px;box-sizing:border-box;align-items:center}
  .member-choice{display:flex;flex-direction:column;align-items:center;gap:4px}
  .member{display:flex;align-items:center;gap:10px;padding:8px 12px;border:1px solid var(--line);border-radius:7px;background:transparent}
  a.member:hover{border-color:var(--line-strong)}
  .member .use{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .active,.member[aria-current],.variant[aria-current]{color:var(--accent);border-color:var(--accent);background:var(--accent-light)}
  .forms-row{font-size:12px;color:var(--muted);align-items:center}
  .forms-row .layer-label{line-height:normal}
  .forms-row small{margin-left:auto}
  .forms-link{color:var(--accent);text-decoration:none}.forms-link:hover{text-decoration:underline}
  .forms-row :global(.cite-link){border:0;background:none;padding:0;font-size:inherit;color:var(--accent)}.forms-row :global(.cite-link:hover){text-decoration:underline}
  .layer-chips{margin:0}
  .variant-chips{display:flex;flex-wrap:wrap;gap:6px;flex:1}
  .variant{display:inline-flex;align-items:baseline;gap:6px;padding:4px 10px;border:1px solid var(--line);border-radius:7px;color:var(--ink);text-decoration:none;font-size:22px;line-height:1.2}
  .variant:hover{border-color:var(--accent)}
  /* A derived chip is a prediction: a small mark after the character says so; a form no character
     has is set smaller, as its sequence is longer than a character. */
  .derived-form{cursor:help;border-radius:3px}.derived-form:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}
  .derived-mark{font-size:11px;color:var(--muted);margin-left:-4px;align-self:flex-start}
  .sharing .derived-mark{margin-left:1px}
  .variant.unencoded :global(.script-text){font-size:15px}
  .variant.unencoded:hover{border-color:var(--accent)}
  .derived{flex-basis:100%}
  .derived summary{cursor:pointer;width:fit-content;font-size:12px;color:var(--muted)}
  .derived summary small{font-variant-numeric:tabular-nums}
  .derived[open] summary{margin-bottom:6px}
  .variant small{font-size:11px;color:var(--muted)}
  .variant.structure{font-size:15px}
  .layout{display:inline-flex;align-items:baseline;gap:6px;padding-left:8px;border-left:1px solid var(--line)}
  .variant.structure:hover{border-color:var(--line)}
  .arrange{align-self:center;border:0;background:transparent;color:var(--accent);padding:0 4px;font-size:12px;text-decoration:underline;text-underline-offset:3px}
  .arrange:hover{color:var(--ink)}
  .variant.structure .sharing :global(.ids-form){margin-inline:4px 0}
  .variant small.share{color:var(--accent)}
  .word-crops{font-size:12px;color:var(--muted);align-self:center}
  .word-reading{font-size:15px;align-self:center;margin-right:4px}
  .variant .code{font-family:"GenZui Sans",ui-monospace,monospace;font-size:10px}
  .include-variants{justify-self:start;margin-left:92px}
  @media(max-width:600px){.include-variants{margin-left:0}}
  .variant-sources{margin:0;padding-left:92px;font-size:11px;color:var(--muted)}
  .variant-sources summary{cursor:pointer;width:fit-content}
  .variant-sources ul{margin:6px 0 0;padding-left:16px;display:grid;gap:3px;overflow-wrap:anywhere}
  .variant-sources b{font-weight:500;color:var(--ink)}
  @media(max-width:600px){.variant-sources{padding-left:0}}
</style>
