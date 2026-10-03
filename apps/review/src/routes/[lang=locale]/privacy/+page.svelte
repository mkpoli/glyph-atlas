<script>
  import Seo from '$components/Seo.svelte'
  import { t, around } from '$lib/i18n.svelte.js'
  const EMAIL = 'contact@glyphatlas.org'
  const UPDATED = '2026-10-03'
  const SECTIONS = [
    ['who', ['body']], ['browsing', ['body', 'measure']], ['saving', ['body']], ['account', ['body', 'methods']],
    ['public', ['body', 'gravatar']], ['processors', ['body']], ['choices', ['body']], ['changes', ['body']],
  ]
</script>

<Seo title={t('privacy.title')} />

<article class="privacy">
  <h1>{t('privacy.title')}</h1>
  <p class="privacy-updated">{t('privacy.updated', { date: UPDATED })}</p>
  {#each SECTIONS as [id, parts] (id)}
    <section>
      <h2>{t(`privacy.${id}.title`)}</h2>
      {#each parts as part (part)}
        {@const [before, after] = around(`privacy.${id}.${part}`, 'email')}
        <p>{before}{#if after || before !== t(`privacy.${id}.${part}`)}<a href={`mailto:${EMAIL}`}>{EMAIL}</a>{after}{/if}</p>
      {/each}
    </section>
  {/each}
</article>

<style>
  .privacy { max-width: 720px; margin: auto; padding: 40px 20px 64px; font-size: 14px; line-height: 1.7; }
  .privacy h1 { font-size: 30px; font-weight: 500; letter-spacing: -.6px; }
  .privacy-updated { color: var(--muted); font-size: 12px; margin: 6px 0 28px; }
  .privacy h2 { font-size: 16px; font-weight: 600; margin: 28px 0 8px; }
  .privacy p + p { margin-top: 10px; }
  .privacy a { text-decoration: underline; text-underline-offset: 2px; }
</style>
