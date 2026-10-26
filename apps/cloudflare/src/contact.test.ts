import { afterEach, describe, expect, it } from 'bun:test';
import { Problem } from './busy';
import { MAX_MESSAGE, MAX_PARSED_MAIL, SPAM_THRESHOLD, htmlText, inboxText, mailText, receiveMail, sendContact } from './contact';

const sent: { url: string; init: RequestInit }[] = [];
const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; sent.length = 0 });
const answering = (status: number) => {
  globalThis.fetch = (async (url: string, init: RequestInit) => { sent.push({ url, init }); return new Response('{}', { status }) }) as typeof fetch;
};
const env = (allowed = true, token: string | undefined = 'secret') => ({
  CONTACT: { limit: async () => ({ success: allowed }) } as unknown as RateLimit,
  MATRIX_HOMESERVER: 'https://matrix.example' as const, MATRIX_INBOX_ROOM: '!room:example' as const, MATRIX_INBOX_TOKEN: token,
}) as Parameters<typeof sendContact>[0];
const form = (value: unknown, origin = 'https://glyphatlas.org') => new Request('https://glyphatlas.org/api/contact', {
  method: 'POST', headers: { 'content-type': 'application/json', origin, 'cf-connecting-ip': '203.0.113.9' }, body: JSON.stringify(value),
});
const refused = async (promise: Promise<unknown>) => { try { await promise } catch (error) { return error as Problem } throw new Error('accepted') };

describe('a contact-form message', () => {
  it('is posted to the inbox room as the inbox account, with how to reply', async () => {
    answering(200);
    const response = await sendContact(env(), form({ message: '  字が違います  ', reply: 'me@example.org', locale: 'ja' }));
    expect(await response.json()).toEqual({ sent: true });
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toStartWith('https://matrix.example/_matrix/client/v3/rooms/!room%3Aexample/send/m.room.message/');
    expect(sent[0].init.method).toBe('PUT');
    expect((sent[0].init.headers as Record<string, string>).authorization).toBe('Bearer secret');
    expect(JSON.parse(sent[0].init.body as string)).toEqual({ msgtype: 'm.text', body: 'Contact form (ja)\nReply to: me@example.org\n\n字が違います' });
  });

  it('may leave no way to reply', () => {
    expect(inboxText('Hello', null, null)).toBe('Contact form\nReply to: anonymous\n\nHello');
  });

  it('is refused when empty, too long, or sent from another site', async () => {
    answering(200);
    expect((await refused(sendContact(env(), form({ message: '   ' })))).status).toBe(422);
    expect((await refused(sendContact(env(), form({ message: 'x'.repeat(MAX_MESSAGE + 1) })))).status).toBe(422);
    expect((await refused(sendContact(env(), form({ message: 'hi' }, 'https://elsewhere.example')))).status).toBe(403);
    expect(sent).toHaveLength(0);
  });

  it('is held to the rate limit before it is read', async () => {
    answering(200);
    expect((await refused(sendContact(env(false), form({ message: 'hi' })))).status).toBe(429);
    expect(sent).toHaveLength(0);
  });

  it('that fills the hidden field is answered as sent and goes nowhere', async () => {
    answering(200);
    expect(await (await sendContact(env(), form({ message: 'buy', website: 'x' }))).json()).toEqual({ sent: true });
    expect(sent).toHaveLength(0);
  });

  it('is reported as undelivered when the homeserver refuses it', async () => {
    answering(500);
    expect((await refused(sendContact(env(), form({ message: 'hi' })))).status).toBe(502);
  });

  it('is refused, and not logged, by a Worker with no inbox token', async () => {
    answering(200);
    const logged: unknown[] = [], log = console.log;
    console.log = (...args: unknown[]) => { logged.push(args) };
    try { expect((await refused(sendContact(env(true, ''), form({ message: 'secret words' })))).status).toBe(503) }
    finally { console.log = log }
    expect(sent).toHaveLength(0);
    expect(JSON.stringify(logged)).not.toContain('secret words');
  });
});

const raw = [
  'From: =?UTF-8?B?5bGx55Sw?= <yamada@example.jp>', 'To: contact@glyphatlas.org', 'Subject: =?UTF-8?B?6Kqk6K2Y44Gu5aCx5ZGK?=',
  'MIME-Version: 1.0', 'Content-Type: text/plain; charset=UTF-8', 'Content-Transfer-Encoding: 8bit', '', '「字」が「宇」になっています。', '',
].join('\r\n');
const mail = (forwarded: string[], source = raw, size = source.length) => ({
  from: 'bounce@example.jp', to: 'contact@glyphatlas.org', raw: new Response(source).body!, rawSize: size,
  headers: new Headers({ from: 'Big Sender <big@example.org>', subject: 'Scans' }), forward: async (to: string) => { forwarded.push(to) }, setReject: () => {}, reply: async () => {},
}) as unknown as ForwardableEmailMessage;

describe('a mail to the contact address', () => {
  it('is forwarded, then posted to the inbox with its sender, subject and text', async () => {
    answering(200);
    const forwarded: string[] = [];
    await receiveMail(mail(forwarded), { ...env(), CONTACT_FORWARD_TO: 'owner@example.org' });
    expect(forwarded).toEqual(['owner@example.org']);
    expect(JSON.parse(sent[0].init.body as string).body).toBe(
      'Mail to contact@glyphatlas.org\nFrom: 山田 <yamada@example.jp>\nSubject: 誤識の報告\n\n「字」が「宇」になっています。');
  });

  it('is still forwarded when the inbox refuses the post', async () => {
    answering(500);
    const forwarded: string[] = [];
    await receiveMail(mail(forwarded), { ...env(), CONTACT_FORWARD_TO: 'owner@example.org' });
    expect(forwarded).toHaveLength(1);
  });

  it('is cut to the message limit in the inbox, and names its attachments', () => {
    const post = mailText('a@b', '', 'x'.repeat(MAX_MESSAGE + 5), 2);
    expect(post).toStartWith('Mail to contact@glyphatlas.org\nFrom: a@b\nSubject: (none)\nAttachments: 2\n\n');
    expect(post).toEndWith('x\n[…]');
  });

  it('that is HTML only is posted as its text', async () => {
    answering(200);
    const html = ['From: a@example.org', 'Subject: hi', 'MIME-Version: 1.0', 'Content-Type: text/html; charset=UTF-8', '',
      '<html><head><style>p{}</style></head><body><p>First &amp; second</p><p>Line<br>break</p></body></html>', ''].join('\r\n');
    await receiveMail(mail([], html), { ...env(), CONTACT_FORWARD_TO: 'owner@example.org' });
    expect(JSON.parse(sent[0].init.body as string).body).toEndWith('\n\nFirst & second\nLine\nbreak');
  });

  it('that is large is forwarded and posted by its headers alone, unparsed', async () => {
    answering(200);
    const forwarded: string[] = [];
    await receiveMail(mail(forwarded, 'not a mail', MAX_PARSED_MAIL + 1), { ...env(), CONTACT_FORWARD_TO: 'owner@example.org' });
    expect(forwarded).toHaveLength(1);
    expect(JSON.parse(sent[0].init.body as string).body).toBe(
      'Mail to contact@glyphatlas.org\nFrom: Big Sender <big@example.org>\nSubject: Scans\n\n(2.0 MB; read it in the forwarded copy)');
  });

  it('strips markup from an HTML part', () => {
    expect(htmlText('<script>x()</script><b>bold</b>&lt;tag&gt;')).toBe('bold<tag>');
  });
});

describe('a mail that looks like spam', () => {
  // Clef's answer for every mail, or a failure.
  const clef = (odds: number | Error, asked: unknown[] = []) => ({ run: async (_model: string, input: unknown) => {
    asked.push(input); if (odds instanceof Error) throw odds; return { answers: { spam: { type: 'noul', noul: odds } } } } });
  const abused = raw.replace('<yamada@example.jp>', '<noreply@appsheet.com>');

  it('is forwarded whole and posted as one line when Clef rates it spam', async () => {
    answering(200);
    const forwarded: string[] = [];
    await receiveMail(mail(forwarded), { ...env(), CONTACT_FORWARD_TO: 'owner@example.org', AI: clef(0.93) } as never);
    expect(forwarded).toEqual(['owner@example.org']);
    expect(JSON.parse(sent[0].init.body as string).body).toBe(
      'Held as likely spam (93%): 山田 <yamada@example.jp> — 誤識の報告. The forwarded copy has the whole mail.');
  });

  it('is posted in full below the threshold, or when Clef cannot answer', async () => {
    for (const odds of [SPAM_THRESHOLD - 0.01, new Error('AI down')]) {
      answering(200);
      await receiveMail(mail([]), { ...env(), CONTACT_FORWARD_TO: 'owner@example.org', AI: clef(odds) } as never);
      expect(JSON.parse(sent.at(-1)!.init.body as string).body).toStartWith('Mail to contact@glyphatlas.org\n');
    }
  });

  it('is held without asking Clef when it comes from an abused sender', async () => {
    answering(200);
    const asked: unknown[] = [];
    await receiveMail(mail([], abused), { ...env(), CONTACT_FORWARD_TO: 'owner@example.org', AI: clef(0, asked) } as never);
    expect(asked).toHaveLength(0);
    expect(JSON.parse(sent[0].init.body as string).body).toStartWith('Held as likely spam (100%): 山田 <noreply@appsheet.com>');
  });

  it('asks Clef about the sender, subject and text', async () => {
    answering(200);
    const asked: any[] = [];
    await receiveMail(mail([]), { ...env(), CONTACT_FORWARD_TO: 'owner@example.org', AI: clef(0.1, asked) } as never);
    expect(asked[0].model).toBe('clef-flash');
    expect(asked[0].state).toEqual({ from: '山田 <yamada@example.jp>', subject: '誤識の報告', text: '「字」が「宇」になっています。\n' });
    expect(asked[0].questions.spam.type).toBe('noul');
  });
});
