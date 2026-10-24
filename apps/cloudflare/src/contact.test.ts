import { afterEach, describe, expect, it } from 'bun:test';
import { Problem } from './busy';
import { MAX_MESSAGE, inboxText, sendContact } from './contact';

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
