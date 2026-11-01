import { describe, expect, it } from 'bun:test';
import { mailLocale } from './auth';
import { MAIL, BACKUP, mailTag, signInMail } from './mail';

const asking = (headers: Record<string, string>) => new Request('https://glyphatlas.org/', { headers });

describe('the sign-in mail', () => {
  it('is written in a language it has, never in what a request names', () => {
    expect(mailLocale(asking({ cookie: 'atlas.locale=ko-Kore' }))).toBe('ko-Kore');
    expect(mailLocale(asking({ 'accept-language': 'zh-TW,zh;q=0.9' }))).toBe('zh-Hant');
    expect(mailLocale(asking({ 'accept-language': 'ja-JP' }))).toBe('ja');
    expect(mailLocale(asking({ cookie: 'atlas.locale=%22%3E%3Ca', 'accept-language': 'en"><a href=//x>' }))).toBe('en');
    expect(signInMail('en"><a href=//x>', '123456', 'glyphatlas.org').html).toContain('<html lang="en">');
  });
  it('ends with the origin-bound code line', () => {
    expect(signInMail('ja', '123456', 'glyphatlas.org').text.trimEnd().endsWith('@glyphatlas.org #123456')).toBe(true);
  });
  it('is English, the site\'s own backup, for a reader whose language it does not have', () => {
    for (const locale of ['fr', 'fr-FR', 'de', 'pag', 'ja-JP', 'zh-TW', '', 'EN', 'xx-YY']) {
      const mail = signInMail(locale, '995110', 'glyphatlas.org');
      expect(mailTag(locale)).toBe('en');
      expect(mail.html).toContain('<html lang="en">');
      expect(mail.html).toContain('GLYPH ATLAS</span>');
      expect(mail.subject).toBe(MAIL.en.subject.replace('{code}', '995110'));
      expect(mail.text.startsWith(MAIL.en.intro)).toBe(true);
    }
    // The backup is a language the mail carries, and the one the site falls back to.
    expect(Object.keys(MAIL)).toContain(BACKUP);
    expect(BACKUP).toBe('en');
  });
  it('keeps a language it has, the region and script a request names already settled by mailLocale', () => {
    expect(mailTag('ja')).toBe('ja');
    expect(mailTag('ja-x-classical')).toBe('ja-x-classical');
    expect(mailTag('ko-Kore')).toBe('ko-Kore');
    expect(mailTag('vi-Hani')).toBe('vi-Hani');
    expect(mailTag(mailLocale(asking({ 'accept-language': 'vi-VN' })))).toBe('vi');
    expect(mailTag(mailLocale(asking({ 'accept-language': 'zh-TW' })))).toBe('zh-Hant');
    expect(mailTag(mailLocale(asking({ 'accept-language': 'de-DE,de;q=0.9,en;q=0.8' })))).toBe('en');
    expect(mailTag(mailLocale(asking({ 'accept-language': 'pt-BR' })))).toBe('en');
    expect(mailTag(mailLocale(asking({})))).toBe('en');
  });
  it('names the site in the reader\'s language, and in letterspaced caps only for English', () => {
    expect(signInMail('ja', '123456', 'glyphatlas.org').html).toContain('字形大図譜</span>');
    expect(signInMail('ko', '123456', 'glyphatlas.org').html).toContain('자형대도보</span>');
    expect(signInMail('en', '123456', 'glyphatlas.org').html).toContain('letter-spacing:1.2px">GLYPH ATLAS</span>');
    // Every language the site has is a language the mail is written in, and each names the site itself.
    for (const locale of Object.keys(MAIL)) {
      expect(signInMail(locale, '123456', 'glyphatlas.org').html).toContain(`${MAIL[locale].name}</span>`);
    }
  });
  it('shows the code in six cells, each carrying one digit', () => {
    const { html } = signInMail('ja', '123456', 'glyphatlas.org');
    expect(html.match(/text-align:center">\d<\/td>/g)).toHaveLength(6);
    expect(html).not.toContain('%7Bcode%7D');
    expect(html).not.toContain('{code}');
  });
  it('carries a six-digit code and a host, and nothing a client could read as markup', () => {
    // The generator draws the code from `0-9` (better-auth's default OTP generator) and the host comes
    // from the request's own origin, so neither can arrive as markup; this holds that in place.
    const html = signInMail('ja', '995110', 'glyphatlas.org').html;
    expect(html).not.toMatch(/<script|\son[a-z]+\s*=/i);
    expect(html).toContain('@glyphatlas.org #995110');
  });
  it('is drawn in the site\'s own tokens, in the light scheme alone', () => {
    const { html } = signInMail('ja', '123456', 'glyphatlas.org');
    expect(html).toContain('content="light"');
    expect(html).toContain('#6356e5');  // --accent
    expect(html).toContain('#fafafa');  // --paper
    expect(html).toContain('#e3e3e7');  // --line
    expect(html).toContain('#77777f');  // --muted
    expect(html).toContain('border-radius:16px');
  });
});
