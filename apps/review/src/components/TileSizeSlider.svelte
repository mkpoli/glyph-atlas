<script>
  /**
   * Shared custom-styled tile-size range slider used by both Explore and Quick Review.
   * Uses a native `<input type="range">` for keyboard/pointer accessibility while syncing
   * `--control-value`, `--attr-min`, and `--attr-max` for cross-browser progress track styling.
   */
  let {
    min = 0,
    max = 6,
    value = 0,
    label = '',
    valuetext = '',
    oninput = () => {},
    class: className = '',
  } = $props()
</script>

<label class={`zoom-control ${className}`.trim()} title={label}>
  <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5" y="5" width="6" height="6" rx="1"/></svg>
  <input
    type="range"
    {min}
    {max}
    step="1"
    {value}
    aria-label={label}
    aria-valuetext={valuetext || undefined}
    style:--attr-min={min}
    style:--attr-max={max}
    style:--control-value={value}
    oninput={event => oninput(Number(event.currentTarget.value))}
  />
  <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2" width="12" height="12" rx="1.5"/></svg>
</label>

<style>
  .zoom-control {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
  }
  :global(html:not([data-hydrated])) .zoom-control { visibility: hidden; }
  .zoom-control svg {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.4;
    flex-shrink: 0;
  }
  /* Brand-consistent custom range slider (separate WebKit and Firefox pseudo-element blocks). */
  .zoom-control input[type="range"] {
    --slider-track-size: 4px;
    --slider-thumb-size: 14px;
    --slider-track-radius: 999px;
    --slider-thumb-radius: 50%;
    --slider-track-color: var(--line-strong);
    --slider-fill-color: var(--accent-solid);
    --slider-thumb-color: var(--accent-solid);
    --slider-direction: to right;
    --slider-progress: calc(progress(var(--control-value), var(--attr-min), var(--attr-max)) * 100%);
    appearance: none;
    inline-size: 88px;
    block-size: 28px;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    cursor: pointer;
  }
  .zoom-control input[type="range"]:dir(rtl) { --slider-direction: to left; }
  @supports not (opacity: progress(1, 0, 100)) {
    .zoom-control input[type="range"] {
      --slider-progress: calc((var(--control-value) - var(--attr-min)) / max(1, var(--attr-max) - var(--attr-min)) * 100%);
    }
  }
  .zoom-control input[type="range"]::-webkit-slider-runnable-track {
    block-size: var(--slider-track-size);
    border: 0;
    border-radius: var(--slider-track-radius);
    background: linear-gradient(var(--slider-direction), var(--slider-fill-color) var(--slider-progress), var(--slider-track-color) var(--slider-progress));
  }
  .zoom-control input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    inline-size: var(--slider-thumb-size);
    block-size: var(--slider-thumb-size);
    margin-block-start: calc((var(--slider-track-size) - var(--slider-thumb-size)) / 2);
    border: 0;
    border-radius: var(--slider-thumb-radius);
    background: var(--slider-thumb-color);
  }
  .zoom-control input[type="range"]::-moz-range-track {
    block-size: var(--slider-track-size);
    border: 0;
    border-radius: var(--slider-track-radius);
    background: var(--slider-track-color);
  }
  .zoom-control input[type="range"]::-moz-range-progress {
    block-size: var(--slider-track-size);
    border-radius: var(--slider-track-radius);
    background: var(--slider-fill-color);
  }
  .zoom-control input[type="range"]::-moz-range-thumb {
    inline-size: var(--slider-thumb-size);
    block-size: var(--slider-thumb-size);
    border: 0;
    border-radius: var(--slider-thumb-radius);
    background: var(--slider-thumb-color);
  }
  .zoom-control input[type="range"]:focus-visible {
    outline: 3px solid var(--focus-ring);
    outline-offset: 2px;
    border-radius: 4px;
  }
  @media (pointer: coarse) {
    .zoom-control input[type="range"] {
      block-size: 36px;
      --slider-thumb-size: 18px;
    }
  }
  @media (max-width: 700px) {
    .zoom-control input[type="range"] { inline-size: 64px; }
  }
</style>
