/** A complete-file unified diff, preserving whether the original ended with a newline. */
export function sourcePatch(files) {
  return files.map(file => {
    const before = file.before == null || file.before === '' ? [] : file.before.replace(/\n$/, '').split('\n')
    const after = file.after === '' ? [] : file.after.replace(/\n$/, '').split('\n')
    const removed = before.map(line => '-' + line).join('\n')
    const added = after.map(line => '+' + line).join('\n')
    return `diff --git a/${file.path} b/${file.path}\n` +
      (file.before === null ? 'new file mode 100644\n' : '') +
      `--- ${file.before === null ? '/dev/null' : `a/${file.path}`}\n+++ b/${file.path}\n` +
      `@@ -${before.length ? 1 : 0},${before.length} +${after.length ? 1 : 0},${after.length} @@\n` +
      (removed ? removed + '\n' : '') +
      (before.length && !file.before.endsWith('\n') ? '\\ No newline at end of file\n' : '') +
      (added ? added + '\n' : '') +
      (after.length && !file.after.endsWith('\n') ? '\\ No newline at end of file\n' : '')
  }).join('')
}
