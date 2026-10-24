<script>
  import { CHATS } from '$lib/community.js'
  import { t, localize } from '$lib/i18n.svelte.js'
</script>

{#each CHATS as chat (chat.name)}<span class="chat"><a href={chat.href} rel="noopener" target="_blank"><svg class="link-mark" viewBox="0 0 24 24" aria-hidden="true"><path d={chat.mark}/></svg>{chat.name}</a></span>{' · '}{/each}<span class="chat"><a href={localize('/contact')}>{t('contact.title')}</a></span>
