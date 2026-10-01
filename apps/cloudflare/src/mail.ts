// The one mail the site sends: a sign-in code. It is written in the reader's site language, and its
// last line is the origin-bound form (`@host #code`) that lets a browser offer the code only on this site.
type Words = { subject: string; intro: string; expiry: string; ignore: string };
export const MAIL: Record<string, Words> = {
  en: { subject: '{code} is your Glyph Atlas sign-in code', intro: 'Enter this code to sign in to Glyph Atlas:',
    expiry: 'It works for ten minutes.', ignore: 'If you did not ask for it, you can ignore this mail.' },
};

export function signInMail(locale: string, code: string, host: string) {
  const words = MAIL[locale] ?? MAIL[locale.split('-')[0]] ?? MAIL.en;
  const subject = words.subject.replace('{code}', code);
  const text = `${words.intro}\n\n${code}\n\n${words.expiry}\n${words.ignore}\n\n@${host} #${code}\n`;
  const html = `<!doctype html><html lang="${locale}"><body style="margin:0;padding:32px 16px;background:#fafafa;font-family:Inter,'Helvetica Neue',Arial,sans-serif;color:#19191c">
<div style="max-width:440px;margin:auto;background:#fff;border:1px solid #e3e3e7;border-radius:12px;padding:32px">
<p style="margin:0 0 4px;font-size:11px;letter-spacing:1.5px;color:#77777f">GLYPH ATLAS</p>
<p style="margin:16px 0;font-size:15px;line-height:1.5">${words.intro}</p>
<p style="margin:24px 0;font:600 34px/1 ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:10px;color:#6356e5">${code}</p>
<p style="margin:0;font-size:13px;line-height:1.6;color:#77777f">${words.expiry}<br>${words.ignore}</p>
</div><p style="margin:16px auto 0;max-width:440px;font-size:11px;color:#a0a0a7">@${host} #${code}</p></body></html>`;
  return { subject, text, html };
}
