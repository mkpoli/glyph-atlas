<script>
  // The grapheme a crop is read as (字種): the one its character is filed under, each other grapheme the
  // character writes (后 writes 後 too), and any found by search, for a scribe who wrote one character
  // for another. A click saves at once and files the crop under it; clicking one's own choice again
  // takes it back, and the crop is filed by its character again.
  import CharacterSearch from './CharacterSearch.svelte'
  import ScriptText from './ScriptText.svelte'
  import { graphemeChoices, graphemeOf, setGrapheme } from '../lib/cropGraphemes.js'
  import { t } from '../lib/i18n.svelte.js'
  import { signedInUser } from '../lib/session.svelte.js'

  let { item, disabled = false, working = null, saved = null } = $props()
  let offered = $state({ char: null, own: null, writes: [] }), found = $state([])
  let searching = $state(false), query = $state(''), error = $state(''), pending = $state(false)
  const written = $derived(item.written_character ?? item.label ?? '')
  $effect(() => {
    const char = written
    found = []; searching = false; query = ''; error = ''
    graphemeChoices(char).then(choices => { if (char === written) offered = { char, ...choices } })
      .catch(e => { if (char === written) error = e.message })
  })
  const claim = $derived(item.grapheme_claim)
  const disputed = $derived(claim?.status === 'disputed')
  // The grapheme the crop is filed under: the claimed one, or its character's while nobody has claimed
  // one or the claims disagree.
  const current = $derived(claim?.values?.length && !disputed ? claim.values[0].grapheme : offered.char === written ? offered.own?.code_point : null)
  const me = $derived(signedInUser()?.id ?? null)
  const chips = $derived.by(() => {
    const list = offered.char === written ? [offered.own, ...offered.writes].filter(Boolean) : []
    for (const value of [...(claim?.values ?? []).map(v => ({ char: v.char, code_point: v.grapheme })), ...found])
      if (!list.some(chip => chip.code_point === value.code_point)) list.push(value)
    return list
  })
  const claimed = key => Boolean(claim?.values?.some(v => v.grapheme === key))
  const mine = key => Boolean(me && claim?.values?.some(v => v.grapheme === key && v.by?.includes(me)))

  // A pick from search finds its grapheme first; nothing else saves until it has.
  async function save(key, picked = null) {
    if (disabled || pending) return
    pending = true; working?.(true); error = ''
    try {
      if (picked) {
        const head = await graphemeOf(picked)
        if (!chips.some(chip => chip.code_point === head.code_point)) found = [...found, head]
        if (mine(head.code_point)) return
        key = head.code_point
      }
      saved?.(await setGrapheme(item, key, offered.char === written ? offered.own?.code_point : null))
    } catch (e) { error = e.message }
    finally { pending = false; working?.(false) }
  }
  // A click on one's own choice takes it back; any other names that grapheme, the one the crop is
  // already filed under included, which then rests on this reader's claim too.
  const choose = chip => save(mine(chip.code_point) ? null : chip.code_point)
  function pick(result) { searching = false; query = ''; save(null, result) }
</script>

<div class="crop-grapheme">
  <div class="grapheme-row" role="group" aria-label={t('chips.grapheme')}>
    <span class="grapheme-label">{t('chips.grapheme')}</span>
    {#each chips as chip (chip.code_point)}
      <button type="button" class="grapheme-chip" class:current={chip.code_point === current} class:claimed={claimed(chip.code_point)}
        aria-pressed={chip.code_point === current} disabled={disabled || pending} title={chip.code_point} onclick={() => choose(chip)}>
        <ScriptText text={chip.char} titled={false} />
      </button>
    {/each}
    {#if disputed}<span class="grapheme-note">{t('grapheme.disputed')}</span>{/if}
    <button type="button" class="grapheme-find" disabled={disabled || pending} aria-expanded={searching} aria-label={t('grapheme.find')} title={t('grapheme.find')}
      onclick={() => searching = !searching}>⌕</button>
  </div>
  {#if searching}<CharacterSearch compact codePoints autofocus bind:value={query} label={t('grapheme.find')} placeholder={t('search.placeholder')} onselect={pick} />{/if}
  {#if error}<span class="grapheme-error" role="alert">{error}</span>{/if}
</div>

<style>
  .crop-grapheme{display:flex;flex-direction:column;gap:8px;margin:0 0 10px}
  .grapheme-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
  .grapheme-label{min-width:3em;font-size:12px;color:var(--muted)}
  .grapheme-chip{display:inline-flex;align-items:center;padding:4px 10px;font-size:22px;line-height:1.2;
    font-family:"Kureedo Kata","Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans",serif}
  .grapheme-chip:not(:disabled):hover{border-color:var(--accent)}
  .grapheme-chip.current{border:2px solid var(--accent);padding:3px 9px;background:var(--accent-light)}
  .grapheme-chip.claimed:not(.current){border-style:dashed;border-color:var(--accent)}
  .grapheme-note{font-size:12px;color:var(--muted)}
  .grapheme-find{min-width:40px;padding:4px 10px;font-size:18px;line-height:1.2;color:var(--muted);border-style:dashed}
  .grapheme-find[aria-expanded="true"]{color:var(--accent);border-color:var(--accent)}
  .crop-grapheme :global(.character-search){width:100%;min-width:0}
  .grapheme-error{font-size:12px;color:var(--wrong)}
  @media(max-width:700px){.grapheme-chip{font-size:19px;padding:3px 8px}.grapheme-chip.current{padding:2px 7px}}
</style>
