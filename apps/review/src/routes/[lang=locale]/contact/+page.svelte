<script>
  import Seo from '$components/Seo.svelte'
  import { t, around } from '$lib/i18n.svelte.js'
  import { CHATS, OTHER_GROUPS, SPACE } from '$lib/community.js'
  const EMAIL = 'contact@glyphatlas.org'
  const REPO = 'https://github.com/mkpoli/glyph-atlas'
  // Matrix apps someone can join the space with besides Element on the web.
  const APPS = [
    ['Element', 'https://element.io/download'], ['FluffyChat', 'https://fluffychat.im/'], ['Cinny', 'https://cinny.in/'],
  ]
  const email = $derived(around('contact.email.body', 'email'))
  const apps = $derived(around('contact.matrix.apps', 'space'))
</script>

<Seo title={t('contact.title')} description={t('contact.intro')} />

<article class="contact">
  <h1>{t('contact.title')}</h1>
  <p class="contact-intro">{t('contact.intro')}</p>

  <section>
    <h2>{t('contact.chat.title')}</h2>
    <p>{t('contact.chat.body')}</p>
    <ul class="doors">
      {#each CHATS as chat (chat.name)}
        <li><a href={chat.href} rel="noopener" target="_blank"><svg viewBox="0 0 24 24" aria-hidden="true"><path d={chat.mark}/></svg>{chat.name}</a></li>
      {/each}
    </ul>
    <p>{t('contact.chat.rooms')}</p>
  </section>

  <section>
    <h2>{t('contact.matrix.title')}</h2>
    <ol>
      <li>{t('contact.matrix.open')}</li>
      <li>{t('contact.matrix.account')}</li>
    </ol>
    <p>{apps[0]}<code>{SPACE}</code>{apps[1]}</p>
    <ul class="apps">
      {#each APPS as [name, href] (name)}<li><a {href} rel="noopener" target="_blank">{name} ↗</a></li>{/each}
      <li><a href={`https://matrix.to/#/${SPACE}`} rel="noopener" target="_blank">{t('contact.matrix.choose')} ↗</a></li>
    </ul>
  </section>

  <section>
    <h2>{t('contact.others.title')}</h2>
    <p>{t('contact.others.body')}</p>
    <ul class="doors">
      {#each OTHER_GROUPS as group (group.href)}
        <li><a href={group.href} rel="noopener" target="_blank"><svg viewBox="0 0 24 24" role="img" aria-label={group.network}><path d={group.mark}/></svg><span lang={group.lang}>{group.name}</span><small lang={group.lang}>{group.language}</small></a></li>
      {/each}
    </ul>
  </section>

  <section>
    <h2>GitHub</h2>
    <p>{t('contact.github.body')}</p>
    <ul class="apps">
      <li><a href={REPO} rel="noopener" target="_blank">{t('contact.github.repo')} ↗</a></li>
      <li><a href={`${REPO}/issues/new`} rel="noopener" target="_blank">{t('contact.github.issue')} ↗</a></li>
    </ul>
  </section>

  <section>
    <h2>{t('contact.email.title')}</h2>
    <p>{email[0]}<a href={`mailto:${EMAIL}`}>{EMAIL}</a>{email[1]}</p>
  </section>
</article>

<style>
  .contact { max-width: 720px; margin: auto; padding: 40px 20px 64px; font-size: 14px; line-height: 1.7; }
  .contact h1 { font-size: 30px; font-weight: 500; letter-spacing: -.6px; }
  .contact-intro { color: var(--muted); margin: 6px 0 12px; }
  .contact h2 { font-size: 16px; font-weight: 600; margin: 28px 0 8px; }
  .contact p + p, .contact ul + p, .contact ol + p { margin-top: 10px; }
  .contact p a, .contact .apps a { text-decoration: underline; text-underline-offset: 2px; }
  .contact ol { padding-left: 1.4em; display: grid; gap: 4px; }
  .contact code { font-size: 13px; padding: 1px 5px; border-radius: 4px; background: var(--surface-sunken); }
  .doors { list-style: none; padding: 0; margin: 12px 0; display: flex; flex-wrap: wrap; gap: 8px; }
  .doors a { display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px; border: 1px solid var(--line); background: var(--surface); border-radius: 8px; font-weight: 500; }
  .doors a:hover { border-color: var(--line-hover); }
  .doors svg { width: 18px; height: 18px; fill: currentColor; }
  .doors small { color: var(--muted); font-weight: 400; }
  .apps { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 4px 16px; }
</style>
