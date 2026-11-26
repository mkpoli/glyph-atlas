<script>
  // A run as the page writes it, under its ruby, and the notes of how it is read, each with what it rests on, on
  // one line. Where readers give the run more than one ruby, each stands beside it. A run the transcription gives,
  // and no reader yet, says so.
  import { t } from '../lib/i18n.svelte.js'
  import ReadingRuby from './ReadingRuby.svelte'
  let { entry } = $props()
</script>

<div class="reading-entry" class:transcribed={entry.transcribed}>
  <ReadingRuby value={entry.ruby.length === 1 ? entry.ruby[0] : { run: entry.run.run, at: entry.run.at }} />
  {#if entry.ruby.length > 1}{#each entry.ruby as value}<bdi class="entry-ruby" title={t('reading.ruby')}>{value.ruby.join('')}</bdi>{/each}{/if}
  {#if entry.transcribed}<span>{t('reading.transcribed')}</span>{/if}
  {#each entry.left as value}<span>{t('reading.left')} <bdi class="entry-ruby">{value.ruby.join('')}</bdi></span>{/each}
  {#each entry.notes as value}<span>{t('reading.label')} <bdi class="entry-note">{value.reading.join('')}</bdi></span>{#if value.methods?.[0]}<span title={t('reading.method')}>{t(`reading.methods.${value.methods[0]}`)}</span>{/if}{/each}
</div>

<style>
  .reading-entry{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline;min-width:0;font-size:12px;color:var(--muted)}
  .reading-entry :global(.reading-run){font-family:var(--char-font);font-size:28px;line-height:1.3;color:var(--ink)}
  .entry-ruby{font-family:var(--char-font);font-size:15px;color:var(--ink)}
  .entry-note{font-size:14px;color:var(--ink)}
</style>
