<script>
  // The header's Sponsor pill, led by the author's face, and the dialog it opens: the author's
  // portrait, why they need help, then three steps that end at the Ko-fi button. Ko-fi handles the
  // amount and the payment; companies can write in for an ad on the site.
  import { t } from '$lib/i18n.svelte.js'
  let open = $state(false), dialog = $state()
  $effect(() => { if (open && dialog && !dialog.open) dialog.showModal() })
  const heart = 'M12 21s-8.5-5.1-8.5-11.3A4.7 4.7 0 0 1 12 6.9a4.7 4.7 0 0 1 8.5 2.8C20.5 15.9 12 21 12 21Z'
</script>

<button class="sponsor" aria-label={t('sponsor.label')} aria-haspopup="dialog" aria-expanded={open} onclick={() => open = true}>
  <span class="face" aria-hidden="true"><img src="/sponsor-face.webp" alt="" width="24" height="24" decoding="async" /><svg class="badge" viewBox="0 0 24 24"><path d={heart} /></svg></span>
  <span class="label">{t('sponsor.label')}</span>
</button>

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
  <dialog class="sponsor-dialog" bind:this={dialog} aria-labelledby="sponsor-title" onclose={() => open = false} onclick={event => { if (event.target === dialog) dialog.close() }}>
    <header><h2 id="sponsor-title">{t('sponsor.label')}</h2><button class="close" aria-label={t('sponsor.close')} onclick={() => dialog.close()}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg></button></header>
    <div class="sheet">
      <div class="hero" aria-hidden="true">
        <span class="portrait"><img src="/sponsor-portrait.webp" alt="" width="112" height="112" decoding="async" /><svg class="heart" viewBox="0 0 24 24"><path d={heart} /><ellipse class="shine" cx="7.6" cy="9.6" rx="1.5" ry="2.1" transform="rotate(-30 7.6 9.6)" /></svg></span>
        <span class="spark s1">✦</span><span class="spark s2">✦</span><span class="spark s3">✦</span>
      </div>
      <p class="lead">{t('sponsor.lead')}</p>
      <p class="ask">{t('sponsor.ask')}</p>
      <section class="how">
        <h3>{t('sponsor.howTo')}</h3>
        <ol class="steps" role="list"><li>{t('sponsor.step1')}</li><li>{t('sponsor.step2')}</li><li>{t('sponsor.step3')}</li></ol>
      </section>
      <a class="kofi" href="https://ko-fi.com/mkpoli" target="_blank" rel="noreferrer">
        <span class="mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9h12v6a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4ZM16 11h2a2.5 2.5 0 0 1 0 5h-2M7 3v3M11 2v3M15 3v3" /></svg></span>
        <span class="copy"><strong>Ko-fi</strong><small>{t('sponsor.kofi')}</small></span>
        <svg class="arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6" /></svg>
      </a>
      <a class="company" href="mailto:mkpoli@mkpo.li?subject=Sponsorship%20on%20glyphatlas.org">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18v12H3zM3 7l9 6 9-6" /></svg>
        <span class="copy"><strong>{t('sponsor.company')}</strong><small>{t('sponsor.companyHint')}</small></span>
      </a>
      <p class="thanks">{t('sponsor.thanks')}</p>
    </div>
  </dialog>
{/if}

<style>
  .sponsor, .sponsor-dialog { --sp-ink: light-dark(#c2185b, #ffb3cd); --sp-heart: light-dark(#ff4f88, #ff7aa6); --sp-line: light-dark(#ffc2d6, #6b2c47);
    --sp-soft: light-dark(#fff1f5, #2f1822); --sp-ring: light-dark(#fff, #3d1a2b); }
  .sponsor { display: inline-flex; align-items: center; gap: 9px; flex: none; height: 38px; padding: 0 15px 0 6px; border: 1px solid var(--sp-line); border-radius: 999px;
    background: linear-gradient(135deg, light-dark(#ffe6ef, #3d1a2b), light-dark(#fff0e0, #3a2318)); color: var(--sp-ink); font-size: 12px; font-weight: 650;
    white-space: nowrap; box-shadow: 0 1px 2px rgb(194 24 91 / .12); transition: transform .18s ease, box-shadow .18s ease; }
  .sponsor:not(:disabled):hover { border-color: var(--sp-line); transform: translateY(-1px); box-shadow: 0 4px 12px rgb(255 79 136 / .28); }
  .sponsor:active { transform: scale(.97); }
  .face { position: relative; flex: none; width: 26px; height: 26px; }
  .face img { display: block; width: 100%; height: 100%; border-radius: 50%; object-fit: cover; box-shadow: 0 0 0 1.5px var(--sp-ring), 0 0 0 2.5px var(--sp-line); }
  .badge { position: absolute; right: -4px; bottom: -3px; width: 12px; height: 12px; fill: var(--sp-heart); stroke: var(--sp-ring); stroke-width: 2.5; paint-order: stroke;
    overflow: visible; transform-origin: 50% 60%; animation: beat 2.8s ease-in-out infinite; }
  .sponsor:hover .badge, .sponsor:focus-visible .badge { animation: bounce .6s ease-in-out infinite; }
  @media (max-width: 700px) { .sponsor { width: 44px; height: 44px; padding: 0; justify-content: center; } .label { display: none; } .face { width: 30px; height: 30px; } }

  .sponsor-dialog { width: min(480px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: 0; border: 1px solid var(--line); border-radius: 16px;
    background: var(--surface); color: var(--ink); box-shadow: 0 30px 90px var(--shadow); overflow: auto; }
  .sponsor-dialog[open] { animation: arrive .32s cubic-bezier(.2, .8, .2, 1); }
  .sponsor-dialog::backdrop { background: var(--backdrop); backdrop-filter: blur(6px); animation: fade .25s ease; }
  header { display: flex; align-items: center; justify-content: space-between; padding: 14px 14px 0 24px; }
  h2 { font-size: 16px; font-weight: 700; }
  .close { display: grid; place-items: center; width: 36px; height: 36px; padding: 0; border: 0; border-radius: 50%; background: transparent; color: var(--muted); }
  .close:not(:disabled):hover { background: var(--surface-selected); color: var(--ink); }
  .close svg { width: 18px; height: 18px; }
  .sheet { display: flex; flex-direction: column; gap: 14px; padding: 4px 24px 22px; }
  .hero { position: relative; display: grid; place-items: center; height: 136px; border-radius: 16px; background: radial-gradient(closest-side, light-dark(#ffd9e5, #4a1e31), transparent); }
  .portrait { position: relative; width: 112px; height: 112px; }
  .portrait img { display: block; width: 100%; height: 100%; border-radius: 50%; object-fit: cover;
    box-shadow: 0 0 0 4px var(--surface), 0 0 0 6px var(--sp-line), 0 10px 24px rgb(255 79 136 / .3); }
  .heart { position: absolute; right: -2px; bottom: 2px; width: 34px; height: 34px; fill: var(--sp-heart); stroke: var(--surface); stroke-width: 2; paint-order: stroke;
    overflow: visible; transform-origin: 50% 60%; animation: beat 2.8s ease-in-out infinite; filter: drop-shadow(0 3px 6px rgb(255 79 136 / .35)); }
  .heart .shine { fill: #fff; stroke: none; opacity: .6; }
  .spark { position: absolute; color: var(--sp-heart); opacity: .75; animation: twinkle 2.4s ease-in-out infinite; }
  .s1 { top: 22px; left: calc(50% - 84px); font-size: 14px; }
  .s2 { top: 12px; left: calc(50% + 66px); font-size: 10px; animation-delay: .8s; }
  .s3 { bottom: 20px; left: calc(50% - 76px); font-size: 11px; animation-delay: 1.6s; }
  .lead { font-size: 14px; line-height: 1.65; }
  .ask { font-size: 14.5px; font-weight: 650; line-height: 1.5; color: var(--sp-ink); }
  .sheet:lang(ko) { word-break: keep-all; }
  .how { padding: 12px 14px; border: 1px dashed var(--sp-line); border-radius: 14px; background: var(--sp-soft); }
  h3 { margin: 0 0 8px; font-size: 12px; font-weight: 700; color: var(--sp-ink); }
  .steps { display: flex; flex-direction: column; gap: 6px; margin: 0; padding: 0; list-style: none; counter-reset: step; }
  .steps li { display: flex; align-items: baseline; gap: 8px; font-size: 13px; line-height: 1.45; counter-increment: step; }
  .steps li::before { content: counter(step); display: grid; place-items: center; flex: none; width: 20px; height: 20px; border-radius: 50%;
    background: var(--sp-ink); color: var(--sp-soft); font-size: 11px; font-weight: 700; line-height: 1; transform: translateY(-1px); }
  .kofi { display: flex; align-items: center; gap: 12px; padding: 12px 18px 12px 12px; border-radius: 999px; background: linear-gradient(135deg, #d81b60, #b0124f);
    color: #fff; box-shadow: 0 6px 16px rgb(216 27 96 / .32); transition: transform .18s ease, box-shadow .18s ease; }
  .kofi:hover { transform: translateY(-1px); box-shadow: 0 8px 22px rgb(216 27 96 / .42); }
  .kofi:active { transform: scale(.98); }
  .mark { display: grid; place-items: center; flex: none; width: 38px; height: 38px; border-radius: 50%; background: rgb(255 255 255 / .22); }
  .mark svg { width: 20px; height: 20px; }
  .arrow { flex: none; width: 18px; height: 18px; }
  .copy { display: flex; flex: 1; flex-direction: column; gap: 1px; min-width: 0; }
  .copy strong { font-size: 13.5px; font-weight: 700; }
  .copy small { font-size: 12px; line-height: 1.4; }
  .kofi .copy strong { font-size: 15px; }
  .company { display: flex; align-items: flex-start; gap: 10px; padding: 8px 12px; border-radius: 12px; color: var(--muted); }
  .company:hover { background: var(--surface-selected); color: var(--ink); }
  .company svg { flex: none; width: 16px; height: 16px; margin-top: 2px; }
  .company .copy strong { font-size: 12.5px; color: var(--ink); }
  .thanks { font-size: 12.5px; line-height: 1.5; color: var(--muted); text-align: center; }
  @keyframes beat { 0%, 70%, 100% { transform: scale(1) } 78% { transform: scale(1.18) } 86% { transform: scale(.96) } 93% { transform: scale(1.1) } }
  @keyframes bounce { 0%, 100% { transform: translateY(0) scale(1) } 40% { transform: translateY(-2px) scale(1.18) rotate(-8deg) } }
  @keyframes twinkle { 0%, 100% { opacity: .2; transform: scale(.7) } 50% { opacity: .9; transform: scale(1) } }
  @keyframes arrive { from { opacity: 0; transform: translateY(14px) scale(.98) } }
  @keyframes fade { from { opacity: 0 } }
</style>
