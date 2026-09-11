<script>
  /**
   * The 字母 picker: the code points the focused unit's reading may have been written with.
   *
   * The list is `GET /units/{id}/candidates`: the ordinary kana of the reading first, then every
   * hentaigana with that 音価, each with its 字母, its NINJAL reference glyph and the T31 score where
   * the unit carries one. Choosing one records `unicode`, `jibo` and `script` as reviews.
   */
  let { session } = $props()

  let index = $state(0)

  const data = $derived(session.candidates)
  const candidates = $derived(data?.candidates ?? [])

  function choose(candidate) {
    session.chooseCandidate(candidate)
  }

  function onkeydown(event) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      session.cancel()
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      index = Math.min(candidates.length - 1, index + 1)
      event.preventDefault()
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      index = Math.max(0, index - 1)
      event.preventDefault()
    } else if (event.key === 'Enter') {
      choose(candidates[index])
      event.preventDefault()
    } else if (/^[1-9]$/.test(event.key)) {
      const candidate = candidates[Number(event.key) - 1]
      if (candidate) choose(candidate)
    }
  }

  function percent(score) {
    return `${Math.round(score * 100)}%`
  }
</script>

<svelte:window onkeydown={onkeydown} />

<div
  class="overlay"
  role="presentation"
  onclick={(event) => {
    if (event.target === event.currentTarget) session.cancel()
  }}
>
  <div class="dialog" role="dialog" aria-modal="true" aria-label="字母" tabindex="-1">
    <h2>字母 — the code point of the focused unit</h2>
    {#if data}
      <p class="small muted">
        reading <strong class="tagline">{data.reading ?? '—'}</strong> · current
        <code>{data.unicode ?? '—'}</code> {#if data.jibo}· 字母 <span class="tagline">{data.jibo}</span>{/if}
        · {data.classification}
      </p>
      <div class="candidates">
        {#each candidates as candidate, position (candidate.unicode)}
          <button
            class="candidate {candidate.current ? 'current' : ''}"
            class:focused={position === index}
            style={position === index ? 'outline:2px solid var(--accent)' : ''}
            onclick={() => choose(candidate)}
            onpointerenter={() => (index = position)}
            title={`${candidate.unicode}${candidate.jibo ? ` — 字母 ${candidate.jibo}` : ''}${
              candidate.score != null ? ` · score ${percent(candidate.score)}` : ''
            }${candidate.mj ? ` · ${candidate.mj}` : ''}`}
          >
            {#if candidate.reference_url}
              <img src={candidate.reference_url} alt="" loading="lazy" />
            {:else}
              <span class="glyph">{candidate.char ?? '□'}</span>
            {/if}
            <span class="jibo">{candidate.jibo ?? candidate.char ?? '—'}</span>
            <span class="meta">{candidate.unicode}</span>
            {#if candidate.score != null}
              <span class="meta">p {percent(candidate.score)}</span>
            {/if}
            {#if candidate.current}
              <span class="badge ok">current</span>
            {/if}
          </button>
        {/each}
      </div>
      {#if !candidates.length}
        <p class="muted">the reading has no candidate code point.</p>
      {/if}
      <p class="small muted" style="margin-bottom:0">
        <kbd>1</kbd>…<kbd>9</kbd> or the arrows and <kbd>enter</kbd> choose; <kbd>escape</kbd> cancels.
        Choosing records <code>unicode</code>, <code>jibo</code> and <code>script</code> as reviews.
      </p>
    {:else}
      <p class="muted">loading the candidates…</p>
    {/if}
  </div>
</div>
