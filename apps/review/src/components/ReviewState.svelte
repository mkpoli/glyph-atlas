<script>
  // How far a crop has been checked, beside its title in the inspector. Every crop says it: a crop no
  // reviewer has passed reads as not checked yet, so a glance tells it from one that has.
  // `tone` is checked, flagged, waiting (a state that holds the crop back) or unchecked.
  let { tone = 'unchecked', text, title = null } = $props()
</script>

<span class="review-state {tone}" {title}>{#if tone === 'checked'}<span aria-hidden="true">✓</span>{/if}{text}</span>

<style>
  .review-state{display:inline-flex;align-items:center;gap:5px;font-size:11px;padding:4px 10px;border-radius:20px;line-height:1.3;text-wrap:balance;border:1px solid transparent}
  .checked{color:var(--good);background:var(--good-light);border-color:color-mix(in srgb, var(--good) 35%, transparent);font-weight:500}
  .flagged{color:var(--wrong);background:var(--wrong-light)}
  .waiting{color:var(--uncertain);background:var(--uncertain-light)}
  .unchecked{color:var(--muted);border:1px dashed var(--line-strong)}
</style>
