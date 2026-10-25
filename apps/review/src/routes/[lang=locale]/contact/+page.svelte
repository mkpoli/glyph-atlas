<script>
  import Seo from '$components/Seo.svelte'
  import LinkMark from '$components/LinkMark.svelte'
  import { t, around, locale } from '$lib/i18n.svelte.js'
  import { CHATS, IRC_CHANNELS, IRC_WEB, OTHER_GROUPS, SPACE } from '$lib/community.js'
  const EMAIL = 'contact@glyphatlas.org'
  const REPO = 'https://github.com/mkpoli/glyph-atlas'
  // Matrix apps someone can join the space with besides Element on the web.
  const APPS = [
    ['Element', 'https://element.io/download'], ['FluffyChat', 'https://fluffychat.im/'], ['Cinny', 'https://cinny.in/'],
  ]
  const email = $derived(around('contact.email.body', 'email'))
  const failed = $derived(around('contact.form.failed', 'email'))
  // The message form posts to the Worker, which hands it to the maintainers' Matrix inbox; no session
  // is started for it. `website` is a field people never see (`contact.ts`).
  let message = $state(''), reply = $state(''), website = $state('')
  let status = $state('idle')
  async function send(event) {
    event.preventDefault()
    if (status === 'sending' || !message.trim()) return
    status = 'sending'
    try {
      const response = await fetch('/api/contact', { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ message, reply, website, locale: locale() }) })
      status = response.ok ? 'sent' : response.status === 429 ? 'busy' : 'failed'
      if (response.ok) { message = ''; reply = '' }
    } catch { status = 'failed' }
  }
  const apps = $derived(around('contact.matrix.apps', 'space'))
  // The IRC picker starts on the channel for the page's language; lzh pages are Chinese.
  const spoken = locale() === 'lzh' ? 'zh' : locale().split('-')[0]
  let picked = $state(IRC_CHANNELS.filter(item => item.language === spoken).map(item => item.channel))
  const joined = $derived(IRC_CHANNELS.map(item => item.channel).filter(channel => picked.includes(channel)).join(','))
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
        <li><a href={chat.href} rel="noopener" target="_blank"><LinkMark name={chat.mark} />{chat.name}</a></li>
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
    <h2 id="irc">{t('contact.irc.title')}</h2>
    <p>{t('contact.irc.body')}</p>
    <fieldset class="channels" aria-labelledby="irc">
      {#each IRC_CHANNELS as item (item.channel)}
        <label><input type="checkbox" value={item.channel} bind:group={picked} /><span><span lang={item.lang}>{item.name}</span><code>{item.channel}</code></span></label>
      {/each}
    </fieldset>
    {#if joined}
      <div class="irc-open">
        <a class="send" href={IRC_WEB + joined} rel="noopener" target="_blank">{t('contact.irc.open')} ↗</a>
        <code>/join {joined}</code>
      </div>
    {/if}
    <p>{t('contact.irc.history')}</p>
  </section>

  <section>
    <h2>{t('contact.others.title')}</h2>
    <p>{t('contact.others.body')}</p>
    <ul class="doors">
      {#each OTHER_GROUPS as group (group.href)}
        <li><a href={group.href} rel="noopener" target="_blank"><LinkMark name={group.mark} /><span class="visually-hidden">{group.network}: </span><span lang={group.lang}>{group.name}</span><small lang={group.lang}>{group.language}</small></a></li>
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

  <section>
    <h2 id="message">{t('contact.form.title')}</h2>
    <p>{t('contact.form.body')}</p>
    <form class="message-form" onsubmit={send} aria-labelledby="message">
      <label for="contact-message">{t('contact.form.message')}</label>
      <textarea id="contact-message" rows="6" maxlength="4000" required bind:value={message} oninput={() => { if (status !== 'sending') status = 'idle' }}></textarea>
      <label for="contact-reply">{t('contact.form.reply')}</label>
      <input id="contact-reply" maxlength="200" autocomplete="email" placeholder={t('contact.form.replyHint')} bind:value={reply} />
      <input class="unseen" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" bind:value={website} />
      <div class="form-foot">
        <button class="send" type="submit" disabled={status === 'sending' || !message.trim()}>{status === 'sending' ? t('contact.form.sending') : t('contact.form.send')}</button>
        <p class="form-status" role="status">{#if status === 'sent'}{t('contact.form.sent')}{:else if status === 'busy'}{t('contact.form.busy')}{:else if status === 'failed'}{failed[0]}<a href={`mailto:${EMAIL}`}>{EMAIL}</a>{failed[1]}{/if}</p>
      </div>
    </form>
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
  .doors :global(.link-mark) { width: 18px; height: 18px; margin: 0; vertical-align: 0; }
  .doors small { color: var(--muted); font-weight: 400; }
  .message-form { display: grid; gap: 6px; margin-top: 12px; }
  .message-form label { font-weight: 500; margin-top: 6px; }
  .message-form textarea, .message-form input { font: inherit; color: inherit; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; width: 100%; box-sizing: border-box; }
  .message-form textarea { resize: vertical; min-height: 120px; }
  .message-form textarea:focus-visible, .message-form input:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .message-form .unseen { position: absolute; left: -10000px; width: 1px; height: 1px; opacity: 0; }
  .form-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 8px; }
  .send { font: inherit; font-weight: 500; padding: 8px 18px; border-radius: 8px; border: 0; background: var(--accent-solid); color: var(--on-color); cursor: pointer; }
  .send:hover:not(:disabled) { background: var(--accent-solid-hover); }
  .send:disabled { opacity: .5; cursor: default; }
  .form-status { margin: 0; color: var(--muted); }
  .channels { border: 0; padding: 0; margin: 12px 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 6px; }
  .channels label { display: flex; align-items: center; gap: 10px; padding: 6px 12px; border: 1px solid var(--line); background: var(--surface); border-radius: 8px; cursor: pointer; min-width: 0; line-height: 1.4; }
  .channels label:hover { border-color: var(--line-hover); }
  .channels label:has(input:checked) { border-color: var(--accent); }
  .channels input { accent-color: var(--accent-solid); margin: 0; flex-shrink: 0; }
  .channels label > span { display: grid; min-width: 0; }
  .channels code { color: var(--muted); background: none; padding: 0; font-size: 12px; overflow-wrap: anywhere; }
  .irc-open { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin: 4px 0 12px; }
  .irc-open a.send { text-decoration: none; display: inline-block; }
  .irc-open code { overflow-wrap: anywhere; }
  .apps { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 4px 16px; }
</style>
