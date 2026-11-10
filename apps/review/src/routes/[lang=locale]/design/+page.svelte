<script>
  import '@glyph-atlas/design-system/components.css'

  let scheme = $state('system')
  function choose(value) {
    scheme = value
    if (value === 'system') delete document.documentElement.dataset.theme
    else document.documentElement.dataset.theme = value
  }

  const surfaceTokens = ['paper', 'surface', 'surface-sunken', 'surface-subtle', 'surface-tile', 'surface-selected', 'surface-badge', 'surface-quiet']
  const inkTokens = ['ink', 'muted', 'faint', 'ink-soft']
  const lineTokens = ['line', 'line-strong', 'line-hover']
  const accentTokens = ['accent', 'accent-solid', 'accent-solid-hover', 'accent-light', 'accent-tile', 'focus-ring']
  const statusTokens = ['good', 'wrong', 'uncertain', 'proposed']

  const typeScale = [
    { token: '--ga-text-3xl', size: '34px', use: 'Page title' },
    { token: '--ga-text-2xl', size: '25px', use: 'Section title' },
    { token: '--ga-text-xl', size: '17px', use: 'Record title' },
    { token: '--ga-text-lg', size: '14px', use: 'Body, inputs' },
    { token: '--ga-text-base', size: '13px', use: 'Interface copy' },
    { token: '--ga-text-md', size: '12px', use: 'Meta, labels' },
    { token: '--ga-text-sm', size: '11px', use: 'Badges, captions' },
    { token: '--ga-text-xs', size: '10px', use: 'Overline' }
  ]

  const spaceScale = [['1', '4px'], ['2', '8px'], ['3', '12px'], ['4', '16px'], ['5', '20px'], ['6', '24px'], ['7', '32px'], ['8', '40px'], ['9', '48px']]
  const radiusScale = ['xs', 'sm', 'md', 'lg', 'xl', 'full']
</script>

<svelte:head>
  <title>Design system · Glyph Atlas</title>
  <meta name="description" content="Shared tokens and controls for Glyph Atlas surfaces." />
</svelte:head>

{#snippet tick()}
  <svg viewBox="0 0 12 12" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1.8 6.4 4.6 9 10.2 2.8" /></svg>
{/snippet}

<div class="design">
  <header class="hero">
    <p class="overline">Glyph Atlas</p>
    <div class="hero-title">
      <h1>Design system</h1>
      <div class="scheme" role="group" aria-label="Colour scheme">
        {#each ['system', 'light', 'dark'] as value (value)}
          <button class="ga-tab" aria-pressed={scheme === value} onclick={() => choose(value)}>{value}</button>
        {/each}
      </div>
    </div>
    <p class="hero-copy">The colour pairs, type scale, spacing and controls that every Glyph Atlas surface shares. A page, a dialog and this documentation resolve the same token in light and dark.</p>
    <div class="hero-meta">
      <span class="ga-badge ga-badge--live"><span class="ga-badge__dot"></span>Shared package</span>
      <code>@glyph-atlas/design-system</code>
      <span>review · forms · design</span>
    </div>
  </header>

  <nav class="toc" aria-label="Design system sections">
    <div><span>Foundations</span>
      <a href="#colour">Colour</a><a href="#type">Type</a><a href="#space">Space</a>
    </div>
    <div><span>Components</span>
      <a href="#buttons">Buttons</a><a href="#fields">Fields</a><a href="#choice">Choice</a><a href="#status">Status</a><a href="#surfaces">Surfaces</a>
    </div>
    <div><span>Patterns</span>
      <a href="#toolbar">Toolbar</a><a href="#record">Record</a><a href="#empty">Empty</a>
    </div>
  </nav>

  <section id="colour" class="section">
    <header class="section-head"><p class="overline">Foundations</p><h2>Colour</h2><p>Every token is one declaration with a light and a dark value. Filled controls keep their own accent so a white label stays legible in both schemes.</p></header>
    <div class="groups">
      <div class="group"><h3>Surfaces</h3><div class="swatches">{#each surfaceTokens as token (token)}<div class="swatch"><span style={`background:var(--${token})`}></span><code>{token}</code></div>{/each}</div></div>
      <div class="group"><h3>Ink</h3><div class="swatches">{#each inkTokens as token (token)}<div class="swatch"><span style={`background:var(--${token})`}></span><code>{token}</code></div>{/each}</div></div>
      <div class="group"><h3>Lines</h3><div class="swatches">{#each lineTokens as token (token)}<div class="swatch"><span style={`background:var(--${token})`}></span><code>{token}</code></div>{/each}</div></div>
      <div class="group"><h3>Accent</h3><div class="swatches">{#each accentTokens as token (token)}<div class="swatch"><span style={`background:var(--${token})`}></span><code>{token}</code></div>{/each}</div></div>
      <div class="group"><h3>Status</h3><div class="swatches">{#each statusTokens as token (token)}<div class="swatch"><span style={`background:var(--${token})`}></span><code>{token}</code></div>{/each}</div></div>
    </div>
  </section>

  <section id="type" class="section">
    <header class="section-head"><p class="overline">Foundations</p><h2>Type</h2><p>Inter for the interface, a monospace face for ids and overlines, and the CJK stack named by the document language.</p></header>
    <div class="spec">
      {#each typeScale as row (row.token)}
        <div class="type-row">
          <code>{row.token} · {row.size}</code>
          <span style={`font-size:var(${row.token});font-weight:${row.size === '34px' || row.size === '25px' ? 500 : 400}`}>字形大圖譜 · 珍字集覽</span>
          <small>{row.use}</small>
        </div>
      {/each}
    </div>
  </section>

  <section id="space" class="section">
    <header class="section-head"><p class="overline">Foundations</p><h2>Space and radius</h2><p>A 4px step for gaps and padding; controls use a 6–7px radius, cards 10px, dialogs 14px.</p></header>
    <div class="split">
      <div class="spec">
        {#each spaceScale as [step, size] (step)}
          <div class="space-row"><code>--ga-space-{step}</code><span style={`width:var(--ga-space-${step})`}></span><small>{size}</small></div>
        {/each}
      </div>
      <div class="spec">
        {#each radiusScale as radius (radius)}
          <div class="radius-row"><code>--ga-radius-{radius}</code><span style={`border-radius:var(--ga-radius-${radius})`}></span></div>
        {/each}
      </div>
    </div>
  </section>

  <section id="buttons" class="section">
    <header class="section-head"><p class="overline">Components</p><h2>Buttons</h2><p>One primary action per view. Everything else is a surface button or a quiet link.</p></header>
    <div class="demo">
      <div class="demo-row">
        <button class="ga-button ga-button--primary">Save review <span aria-hidden="true">→</span></button>
        <button class="ga-button">Load more</button>
        <button class="ga-button ga-button--quiet">Skip this crop</button>
        <button class="ga-button ga-button--icon" aria-label="Menu">···</button>
        <button class="ga-button" disabled>Disabled</button>
      </div>
      <div class="demo-row">
        <a class="ga-button ga-button--primary" href="#buttons">Link button <span aria-hidden="true">↗</span></a>
        <a class="ga-button ga-button--quiet" href="#buttons">Quiet link</a>
      </div>
      <pre><code>&lt;button class="ga-button ga-button--primary"&gt;Save review&lt;/button&gt;</code></pre>
    </div>
  </section>

  <section id="fields" class="section">
    <header class="section-head"><p class="overline">Components</p><h2>Fields</h2><p>Label, control and hint stack in one grid. Errors use the wrong colour and a message, never the border alone.</p></header>
    <div class="demo grid">
      <label class="ga-field"><span class="ga-label">Collection</span><input class="ga-input" placeholder="e.g. 康熙字典" /><span class="ga-hint">The work or collection the crop comes from.</span></label>
      <label class="ga-field"><span class="ga-label">Review round</span><select class="ga-select"><option>All rounds</option><option>Round 3</option></select></label>
      <label class="ga-field"><span class="ga-label">Note</span><textarea class="ga-textarea" rows="3">The right-hand stroke is shorter in this impression.</textarea></label>
      <label class="ga-field"><span class="ga-label">Character</span><input class="ga-input" aria-invalid="true" value="U+2A6A5" aria-describedby="char-error" /><span class="ga-error" id="char-error">Use a character or a code point, not both.</span></label>
      <label class="ga-field"><span class="ga-label">Disabled</span><input class="ga-input" value="Locked while saving" disabled /></label>
    </div>
  </section>

  <section id="choice" class="section">
    <header class="section-head"><p class="overline">Components</p><h2>Choice</h2><p>A checkbox keeps its native target; a category is one pill with the checkbox in it, so a row of twenty stays scannable and the checked state is visible without colour.</p></header>
    <div class="demo">
      <h3>Checkbox and radio</h3>
      <div class="demo-row">
        <label class="ga-choice"><input type="checkbox" checked /> Include crops with no text</label>
        <label class="ga-choice"><input type="checkbox" /> Only flagged</label>
        <label class="ga-choice"><input type="radio" name="ink" checked /> Colour scan</label>
        <label class="ga-choice"><input type="radio" name="ink" /> Ink only</label>
      </div>
      <h3>Category pills</h3>
      <div class="demo-row wrap">
        <label class="ga-pill"><input type="checkbox" checked /><span class="ga-pill__box">{@render tick()}</span>吳語</label>
        <label class="ga-pill"><input type="checkbox" /><span class="ga-pill__box">{@render tick()}</span>閩東語</label>
        <label class="ga-pill"><input type="checkbox" checked /><span class="ga-pill__box">{@render tick()}</span>日本國字</label>
        <label class="ga-pill"><input type="checkbox" /><span class="ga-pill__box">{@render tick()}</span>樂譜</label>
        <label class="ga-pill"><input type="checkbox" disabled /><span class="ga-pill__box">{@render tick()}</span>Disabled</label>
      </div>
      <pre><code>&lt;label class="ga-pill"&gt;&lt;input type="checkbox" checked /&gt;&lt;span class="ga-pill__box"&gt;…&lt;/span&gt;吳語&lt;/label&gt;</code></pre>
    </div>
  </section>

  <section id="status" class="section">
    <header class="section-head"><p class="overline">Components</p><h2>Status</h2><p>A status badge carries its word and a dot; colour reinforces it. The checked states come from the review's own vocabulary.</p></header>
    <div class="demo"><div class="demo-row">
      <span class="ga-badge ga-badge--live"><span class="ga-badge__dot"></span>Live</span>
      <span class="ga-badge ga-badge--checked">✓ Checked</span>
      <span class="ga-badge ga-badge--uncertain">? Uncertain</span>
      <span class="ga-badge ga-badge--proposed">Proposed</span>
      <span class="ga-badge ga-badge--wrong">✕ Wrong</span>
      <span class="ga-badge">Unreviewed</span>
    </div></div>
  </section>

  <section id="surfaces" class="section">
    <header class="section-head"><p class="overline">Components</p><h2>Surfaces</h2><p>Tabs, cards, menus and empty states share one border, one radius and the surface token.</p></header>
    <div class="demo">
      <div class="demo-row">
        <div class="ga-tabs" role="tablist">{#each ['All', 'Unreviewed', 'Flagged'] as tab, i (tab)}<button class="ga-tab" role="tab" aria-selected={i === 0}>{tab}</button>{/each}</div>
      </div>
      <div class="demo-row">
        <article class="ga-card">
          <h3 class="ga-card__title">儂</h3>
          <p class="ga-card__meta">吳語人稱代詞 · 1911 · checked</p>
        </article>
        <div class="ga-menu">
          <span class="ga-menu__label">View</span>
          <button class="ga-menu__item">Collection progress</button>
          <button class="ga-menu__item">Export reviews</button>
          <span class="ga-menu__label">Theme</span>
          <button class="ga-menu__item">System</button>
        </div>
      </div>
    </div>
  </section>

  <section id="toolbar" class="section">
    <header class="section-head"><p class="overline">Patterns</p><h2>Toolbar</h2><p>Search first, then filters, then one quiet action. It sits on a rule above the grid.</p></header>
    <div class="demo">
      <div class="ga-toolbar">
        <input class="ga-input search" placeholder="Find a character or U+2A6A5" aria-label="Find a character" />
        <div class="ga-tabs" role="tablist"><button class="ga-tab" role="tab" aria-selected="true">All</button><button class="ga-tab" role="tab">Unreviewed</button><button class="ga-tab" role="tab">Flagged</button></div>
        <button class="ga-button ga-button--quiet">Shuffle</button>
      </div>
    </div>
  </section>

  <section id="record" class="section">
    <header class="section-head"><p class="overline">Patterns</p><h2>Record</h2><p>The crop first, then the work and the review state. Actions stay under the crop on a narrow screen and beside it on a wide one.</p></header>
    <div class="demo record">
      <div class="record-crop">儂</div>
      <div class="record-body">
        <div class="record-line"><h3>儂</h3><span class="ga-badge ga-badge--checked">✓ Checked</span></div>
        <p class="ga-card__meta">U+5102 · 《吳語詞典》 · 1911 · 12</p>
        <p class="record-note">A dialect pronoun recorded in a Jiangnan dictionary. The right component is written shorter.</p>
        <div class="demo-row"><button class="ga-button ga-button--primary">Looks right</button><button class="ga-button">Flag</button><button class="ga-button ga-button--quiet">Skip</button></div>
      </div>
    </div>
  </section>

  <section id="empty" class="section">
    <header class="section-head"><p class="overline">Patterns</p><h2>Empty</h2><p>One mark, one sentence, one way forward.</p></header>
    <div class="demo">
      <div class="ga-empty"><span class="ga-empty__mark">字</span><p class="ga-empty__title">No crops in this round</p><p class="ga-empty__hint">Load another round or clear the filters to see the collection.</p><button class="ga-button ga-button--primary">Clear filters</button></div>
    </div>
  </section>

  <footer class="design-foot">
    <p>Tokens live in <code>packages/design-system/src/tokens.css</code>; controls in <code>components.css</code>. The review app imports the tokens, this page imports the controls.</p>
    <a class="ga-button ga-button--quiet" href="https://github.com/mkpoli/glyph-atlas">GitHub ↗</a>
  </footer>
</div>

<style>
  .design { max-width: 1240px; margin: 0 auto; padding: 32px 4.4vw 80px; }
  .hero { display: grid; gap: 12px; padding: 24px 0 34px; border-bottom: 1px solid var(--line); }
  .hero-title { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  .hero h1 { font-size: var(--ga-text-3xl); font-weight: 500; letter-spacing: -1.2px; }
  .hero-copy { max-width: 66ch; color: var(--muted); font-size: var(--ga-text-lg); }
  .hero-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; color: var(--muted); font-size: var(--ga-text-md); }
  .hero-meta code { font-family: var(--ga-font-mono); color: var(--ink); }
  .scheme { display: flex; gap: 4px; }
  .toc { position: sticky; top: 0; z-index: 4; display: flex; flex-wrap: wrap; gap: 24px; padding: 12px 0; background: color-mix(in srgb, var(--paper) 92%, transparent); backdrop-filter: blur(8px); border-bottom: 1px solid var(--line); }
  .toc div { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .toc span { color: var(--faint); font-family: var(--ga-font-mono); font-size: 9px; letter-spacing: 1.2px; text-transform: uppercase; }
  .toc a { color: var(--muted); font-size: var(--ga-text-md); }
  .toc a:hover { color: var(--ink); }
  .section { display: grid; gap: 24px; padding: 46px 0; border-bottom: 1px solid var(--line); scroll-margin-top: 64px; }
  .section-head { display: grid; gap: 8px; max-width: 70ch; }
  .section-head h2 { font-size: var(--ga-text-2xl); font-weight: 500; letter-spacing: -.6px; }
  .section-head p { color: var(--muted); font-size: var(--ga-text-lg); }
  .groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 24px; }
  .group h3 { margin-bottom: 10px; color: var(--muted); font-family: var(--ga-font-mono); font-size: 9px; font-weight: 500; letter-spacing: 1.2px; text-transform: uppercase; }
  .swatches { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 10px; }
  .swatch { display: grid; gap: 5px; }
  .swatch span { display: block; height: 48px; border: 1px solid var(--line); border-radius: var(--ga-radius-sm); }
  .swatch code { color: var(--muted); font-family: var(--ga-font-mono); font-size: 10px; }
  .spec { display: grid; gap: 2px; }
  .type-row { display: grid; grid-template-columns: 150px minmax(0, 1fr) 100px; align-items: center; gap: 16px; padding: 12px 0; border-top: 1px solid var(--line); }
  .type-row code, .space-row code, .radius-row code { color: var(--muted); font-family: var(--ga-font-mono); font-size: 10px; }
  .type-row span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .type-row small { color: var(--faint); font-size: 10px; text-align: end; }
  .split { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; }
  .space-row { display: grid; grid-template-columns: 110px auto 40px; align-items: center; gap: 12px; padding: 6px 0; }
  .space-row span { display: block; height: 12px; background: var(--accent); border-radius: 2px; }
  .space-row small { color: var(--faint); font-size: 10px; }
  .radius-row { display: grid; grid-template-columns: 110px auto; align-items: center; gap: 12px; padding: 6px 0; }
  .radius-row span { display: block; width: 84px; height: 40px; border: 1px solid var(--line-strong); background: var(--surface); }
  .demo { display: grid; gap: 16px; }
  .demo.grid { grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 18px; }
  .demo h3 { margin: 0; font-size: var(--ga-text-base); font-weight: 550; }
  .demo-row { display: flex; align-items: center; gap: 10px; }
  .demo-row.wrap { flex-wrap: wrap; }
  .demo pre { margin: 0; overflow-x: auto; padding: 12px 14px; border: 1px solid var(--line); border-radius: var(--ga-radius-sm); background: var(--surface-sunken); color: var(--muted); font-size: 11px; }
  .demo pre code { font-family: var(--ga-font-mono); }
  .search { flex: 1 1 260px; max-width: 420px; }
  .record { display: grid; grid-template-columns: minmax(0, 220px) minmax(0, 1fr); gap: 24px; align-items: start; padding: 20px; border: 1px solid var(--line); border-radius: var(--ga-radius-lg); background: var(--surface); }
  .record-crop { display: grid; place-items: center; aspect-ratio: 1; border-radius: var(--ga-radius-md); background: var(--surface-quiet); color: var(--ink); font-family: "Klee One", serif; font-size: 64px; }
  .record-body { display: grid; gap: 8px; }
  .record-line { display: flex; align-items: center; gap: 10px; }
  .record-line h3 { margin: 0; font-size: var(--ga-text-2xl); font-weight: 500; }
  .record-note { color: var(--muted); font-size: var(--ga-text-base); }
  .design-foot { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-top: 32px; color: var(--muted); font-size: var(--ga-text-md); }
  .design-foot code { font-family: var(--ga-font-mono); color: var(--ink); }
  @media (max-width: 760px) {
    .type-row { grid-template-columns: 1fr; gap: 4px; }
    .type-row small { text-align: start; }
    .split { grid-template-columns: 1fr; }
    .record { grid-template-columns: 1fr; }
    .toc { position: static; }
  }
</style>
