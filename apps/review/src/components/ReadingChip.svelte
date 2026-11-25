<script>
  // A run as the page writes it, under its ruby, and the notes of how it is read, each with what it rests on.
  // Where readers give the run more than one ruby, each stands beside it.
  import { t } from '../lib/i18n.svelte.js'
  import ReadingRuby from './ReadingRuby.svelte'
  let { chip } = $props()
</script>

<span class="reading-chip">
  <ReadingRuby value={chip.ruby.length === 1 ? chip.ruby[0] : { run: chip.run.run, at: chip.run.at }} />
  {#if chip.ruby.length > 1}{#each chip.ruby as value}<bdi class="chip-ruby" title={t('reading.ruby')}>{value.ruby.join('')}</bdi>{/each}{/if}
  {#each chip.left as value}<span class="chip-note"><span>{t('reading.left')}</span><bdi class="chip-ruby">{value.ruby.join('')}</bdi></span>{/each}
  {#each chip.notes as value}<span class="chip-note"><span>{t('reading.label')}</span><bdi>{value.reading.join('')}</bdi>{#if value.methods?.[0]}<span title={t('reading.method')}>{t(`reading.methods.${value.methods[0]}`)}</span>{/if}</span>{/each}
</span>

<style>
  .reading-chip{display:inline-flex;flex-wrap:wrap;gap:4px 12px;align-items:center;border:1px solid var(--line);border-radius:8px;padding:5px 10px;background:var(--surface);color:var(--ink);font-size:12px}
  .reading-chip :global(.reading-run){font-family:var(--char-font);font-size:22px;line-height:1.25}
  .chip-ruby{font-family:var(--char-font);font-size:15px;color:var(--ink)}
  .chip-note{display:inline-flex;flex-wrap:wrap;gap:2px 6px;align-items:baseline;font-size:11px;color:var(--muted)}
  .chip-note bdi{font-size:13px;color:var(--ink)}
</style>
