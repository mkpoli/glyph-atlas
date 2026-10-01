import { describe, expect, it } from 'bun:test';
import { mailLocale } from './auth';
import { signInMail } from './mail';

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
});
