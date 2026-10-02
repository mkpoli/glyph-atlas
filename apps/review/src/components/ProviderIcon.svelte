<script>
  // Provider marks are Simple Icons (CC0-1.0, simpleicons.org), drawn in each brand's colour; GitHub's
  // near-black follows the text instead, so it shows in the dark scheme.
  import { siDiscord, siGithub, siGoogle, siKakaotalk, siLine } from 'simple-icons'
  let { name } = $props()
  const ICONS = { github: [siGithub, 'currentColor'], google: [siGoogle], discord: [siDiscord], line: [siLine], kakao: [siKakaotalk] }
  const [icon, colour] = $derived(ICONS[name] ?? [])
</script>
{#if name === 'passkey'}
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="7.5" r="3.5"/><path d="M3 20c0-3.3 2.7-6 6-6 1.2 0 2.3.3 3.2.9"/><circle cx="17.5" cy="13.5" r="2.5"/><path d="M17.5 16v5m0-2h2"/></svg>
{:else if icon}
  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d={icon.path} fill={colour ?? `#${icon.hex}`} /></svg>
{/if}
