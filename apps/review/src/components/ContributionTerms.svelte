<script>
  // What a save releases, beside the button that saves, for a visitor who has not signed in to an
  // account: the first write starts an anonymous session without passing the sign-in form, which is
  // where an account holder read these terms.
  import { t } from '../lib/i18n.svelte.js'
  import { useSession } from '../lib/session.svelte.js'
  const session = useSession()
</script>

{#if !session?.state.user || session.state.user.anonymous}
  <p class="contribution-terms">{t('signIn.terms')} <a href="https://github.com/mkpoli/glyph-atlas/blob/main/CONTRIBUTING.md" rel="noopener" target="_blank">CONTRIBUTING ↗</a></p>
{/if}

<style>
  .contribution-terms{flex-basis:100%;order:10;margin:0;font-size:11px;line-height:1.5;color:var(--muted);text-align:right}
  .contribution-terms a{text-decoration:underline;text-underline-offset:3px;white-space:nowrap}
</style>
