<script>
  // The grapheme picker under a crop's character (CropTitle): the grapheme its character is filed under,
  // each other grapheme the character writes (后 writes 後 too), any already claimed, and a search for a
  // scribe who wrote one character for another. A click saves at once and files the crop under it;
  // clicking one's own choice again takes it back, and the crop is filed by its character again. The
  // picker floats over the inspector, so opening it moves nothing.
  import CharacterSearch from './CharacterSearch.svelte'
  import ScriptText from './ScriptText.svelte'
  import { graphemeChoices, graphemeOf, setGrapheme } from '../lib/cropGraphemes.js'
  import { t } from '../lib/i18n.svelte.js'
  import { signedInUser } from '../lib/session.svelte.js'

  let { item, char, href = null, disabled = false, working = null, saved = null, close } = $props()
  let offered = $state({ char: null, own: null, writes: [] }), found = $state([])
  let query = $state(''), error = $state(''), pending = $state(false), root = $state(null)
  const written = $derived(item.written_character ?? item.label ?? '')
  $effect(() => {
    const asked = written
    graphemeChoices(asked).then(choices => { if (asked === written) offered = { char: asked, ...choices } })
      .catch(e => { if (asked === written) error = e.message })
  })
  const claim = $derived(item.grapheme_claim)
  const disputed = $derived(claim?.status === 'disputed')
  const ready = $derived(offered.char === written)
  // The grapheme the crop is filed under: the claimed one, or its character's while nobody has claimed
  // one or the claims disagree.
  const current = $derived(claim?.values?.length && !disputed ? claim.values[0].grapheme : ready ? offered.own?.code_point : null)
  const me = $derived(signedInUser()?.id ?? null)
  const chips = $derived.by(() => {
    const list = ready ? [offered.own, ...offered.writes].filter(Boolean) : []
    for (const value of [...(claim?.values ?? []).map(v => ({ char: v.char, code_point: v.grapheme })), ...found])
      if (!list.some(chip => chip.code_point === value.code_point)) list.push(value)
    return list
  })
  const claimed = key => Boolean(claim?.values?.some(v => v.grapheme === key))
  const mine = key => Boolean(me && claim?.values?.some(v => v.grapheme === key && v.by?.includes(me)))

  // A pick from search finds its grapheme first; nothing else saves until it has. A save shuts the picker.
  async function save(key, picked = null) {
    if (disabled || pending) return
    pending = true; working?.(true); error = ''
    let done = false
    try {
      if (picked) {
        const head = await graphemeOf(picked)
        if (!chips.some(chip => chip.code_point === head.code_point)) found = [...found, head]
        if (mine(head.code_point)) return
        key = head.code_point
      }
      saved?.(await setGrapheme(item, key, ready ? offered.own?.code_point : null))
      done = true
    } catch (e) { error = e.message }
    finally { pending = false; working?.(false); if (done) close() }
  }
  // A click on one's own choice takes it back; any other names that grapheme, the one the crop is
  // already filed under included, which then rests on this reader's claim too.
  const choose = chip => save(mine(chip.code_point) ? null : chip.code_point)
  function pick(result) { query = ''; save(null, result) }
  // Escape shuts the picker, from its search too, before it can close the inspector; so does a press
  // outside it.
  function keydown(event) {
    if (event.key !== 'Escape' || event.defaultPrevented) return
    event.preventDefault(); close()
  }
  function pointerdown(event) {
    if (root && !root.contains(event.target) && !event.target.closest?.('.glyph-button')) close(false)
  }
  // The picker takes the focus as it opens; Tab then reaches its graphemes and its search.
  $effect(() => { root?.focus({ preventScroll: true }) })
</script>

<svelte:window onkeydown={keydown} onpointerdown={pointerdown} />

<div class="grapheme-picker" role="dialog" tabindex="-1" aria-label={t('grapheme.change')} bind:this={root}>
  <div class="grapheme-row" role="group" aria-label={t('chips.grapheme')}>
    {#each chips as chip (chip.code_point)}
      <button type="button" class="grapheme-chip" class:current={chip.code_point === current} class:claimed={claimed(chip.code_point)}
        aria-pressed={chip.code_point === current} disabled={disabled || pending} title={chip.code_point} onclick={() => choose(chip)}>
        <ScriptText text={chip.char} titled={false} />
      </button>
    {/each}
    {#if disputed}<span class="grapheme-note">{t('grapheme.disputed')}</span>{/if}
  </div>
  <CharacterSearch compact codePoints bind:value={query} label={t('grapheme.find')} placeholder={t('grapheme.find')} onselect={pick} />
  {#if error}<span class="grapheme-error" role="alert">{error}</span>{/if}
  {#if href}<a class="grapheme-page" {href}>{t('chips.showCharacter', { char })} →</a>{/if}
</div>

<style>
  .grapheme-picker{position:absolute;top:100%;left:0;z-index:6;width:min(360px,calc(100vw - 32px));display:flex;flex-direction:column;gap:10px;
    padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--surface);box-shadow:0 12px 30px var(--shadow-menu)}
  .grapheme-picker:focus{outline:none}
  .grapheme-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:39px}
  .grapheme-chip{display:inline-flex;align-items:center;padding:4px 10px;font-size:22px;line-height:1.2;
    font-family:"Kureedo Kata","Klee One","LXGW WenKai TC","LXGW WenKai","GenZui Sans",serif}
  .grapheme-chip:not(:disabled):hover{border-color:var(--accent)}
  .grapheme-chip.current{border:2px solid var(--accent);padding:3px 9px;background:var(--accent-light)}
  .grapheme-chip.claimed:not(.current){border-style:dashed;border-color:var(--accent)}
  .grapheme-note{font-size:12px;color:var(--muted)}
  .grapheme-picker :global(.character-search){width:100%;min-width:0}
  .grapheme-error{font-size:12px;color:var(--wrong)}
  .grapheme-page{align-self:flex-start;font-size:12px;color:var(--muted)}
  .grapheme-page:hover,.grapheme-page:focus-visible{color:var(--ink)}
</style>
