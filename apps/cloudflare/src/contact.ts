// Messages from the contact form and mail to the contact address. Nothing is stored here: each one is posted
// to the maintainers' inbox room on the community's Matrix server, as the inbox account whose token is
// MATRIX_INBOX_TOKEN. A form sender's network address only keys the rate limit; a mail is also forwarded
// whole to the address in the secret CONTACT_FORWARD_TO. No message text is ever logged. A mail that looks
// like spam is posted as one line instead of in full; its forwarded copy is untouched.
import PostalMime from 'postal-mime';
import { Problem } from './busy';
import { body, text } from './input';
import { addressKey } from './reverse';

export const MAX_MESSAGE = 4000;
export const MAX_REPLY = 200;
/** A mail larger than this is posted by its sender and subject alone; the forwarded copy keeps all of it. */
export const MAX_PARSED_MAIL = 2 * 1024 * 1024;

type InboxEnv = Pick<Env, 'MATRIX_HOMESERVER' | 'MATRIX_INBOX_ROOM'> & { MATRIX_INBOX_TOKEN?: string };
type ContactEnv = InboxEnv & Pick<Env, 'CONTACT'>;
type MailEnv = InboxEnv & { CONTACT_FORWARD_TO?: string; AI?: Pick<Ai, 'run'> };

/** Senders whose mail to this address has only ever been abuse of a public app or form builder. */
export const ABUSED_SENDERS = new Set(['noreply@appsheet.com']);
/** A mail Clef rates spam at this probability or above is held to one line. */
export const SPAM_THRESHOLD = 0.8;
/** Clef-flash reads a mail and answers yes or no with a probability. */
const SPAM_MODEL = '@cf/cloudflare/clef-flash';
const SPAM_QUESTION = {
  type: 'noul',
  instructions: 'Is this mail spam, phishing, a scam or bulk marketing, rather than a message written to the maintainers of Glyph Atlas, a website about historical CJK glyphs?',
  criteria: {
    true: 'Spam, phishing, a scam, a fake invoice or delivery notice, or unsolicited marketing.',
    false: 'A genuine message: a question, a report about the site, a request, an offer to collaborate, a reply, or a notice from a service the site uses.',
  },
};

/** How likely a mail is spam: 1 for an abused sender, Clef's probability otherwise, null when Clef cannot be asked. */
export async function spamOdds(ai: MailEnv['AI'], address: string, mail: { from: string; subject: string; text: string }): Promise<number | null> {
  if (ABUSED_SENDERS.has(address.toLowerCase())) return 1;
  if (!ai) return null;
  try {
    const answer = await ai.run(SPAM_MODEL as never, { model: 'clef-flash', state: { ...mail, text: head(mail.text, MAX_MESSAGE).text },
      questions: { spam: SPAM_QUESTION } } as never) as { answers?: { spam?: { noul?: unknown } } };
    const odds = answer.answers?.spam?.noul;
    return typeof odds === 'number' ? odds : null;
  } catch (error) {
    console.error(JSON.stringify({ event: 'spam_check_failed', error: String(error).slice(0, 200) }));
    return null;
  }
}

/** The one-line inbox post for a mail held as likely spam. */
export function heldText(from: string, subject: string, odds: number): string {
  return `Held as likely spam (${Math.round(odds * 100)}%): ${from} — ${subject || '(no subject)'}. The forwarded copy has the whole mail.`;
}

/** Post one message to the inbox room; false when it could not be delivered. */
export async function postToInbox(env: InboxEnv, post: string): Promise<boolean> {
  if (!env.MATRIX_INBOX_TOKEN) { console.error(JSON.stringify({ event: 'inbox_unset' })); return false }
  const room = encodeURIComponent(env.MATRIX_INBOX_ROOM);
  const sent = await fetch(`${env.MATRIX_HOMESERVER}/_matrix/client/v3/rooms/${room}/send/m.room.message/${crypto.randomUUID()}`, {
    method: 'PUT', headers: { authorization: `Bearer ${env.MATRIX_INBOX_TOKEN}`, 'content-type': 'application/json' },
    body: JSON.stringify({ msgtype: 'm.text', body: post }),
  }).catch(() => null);
  if (!sent?.ok) console.error(JSON.stringify({ event: 'inbox_failed', status: sent?.status ?? null }));
  return !!sent?.ok;
}

/** The inbox post: who to answer, if anyone, then the message as written. */
export function inboxText(message: string, reply: string | null, locale: string | null): string {
  const head = [`Contact form${locale ? ` (${locale})` : ''}`, `Reply to: ${reply || 'anonymous'}`];
  return `${head.join('\n')}\n\n${message}`;
}

export async function sendContact(env: ContactEnv, request: Request): Promise<Response> {
  const { success } = await env.CONTACT.limit({ key: addressKey(request.headers.get('cf-connecting-ip')) });
  if (!success) throw new Problem(429, 'Too many messages at once. Wait a minute and try again.');
  const input = await body(request);
  // A field people never see; a form that fills it is a bot, and it is answered as if it were sent.
  if (input.website) return Response.json({ sent: true });
  const message = text(input.message, MAX_MESSAGE, 'message', true)!;
  const reply = text(input.reply, MAX_REPLY, 'reply');
  const locale = typeof input.locale === 'string' && /^[A-Za-z0-9-]{1,32}$/.test(input.locale) ? input.locale : null;
  // A Worker without the token (a local one, or a deploy missing the secret) cannot deliver, and says so.
  if (!env.MATRIX_INBOX_TOKEN) throw new Problem(503, 'The message form is not set up yet. Write to contact@glyphatlas.org.');
  if (!await postToInbox(env, inboxText(message, reply, locale))) throw new Problem(502, 'The message could not be delivered. Try again later.');
  return Response.json({ sent: true });
}

/** At most `limit` characters of a text, read no further than that. */
function head(value: string, limit: number): { text: string; cut: boolean } {
  let out = '', count = 0;
  for (const char of value) { if (count === limit) return { text: out, cut: true }; out += char; count++ }
  return { text: out, cut: false };
}

/** The readable text of an HTML part, for a mail that has no plain one. */
export function htmlText(html: string): string {
  return html.slice(0, MAX_MESSAGE * 20)
    .replace(/<(style|script|head)\b[\s\S]*?<\/\1>/gi, '')
    .replace(/<br\s*\/?>|<\/(p|div|li|tr|h[1-6])>/gi, '\n')
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&')
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n');
}

/** The inbox post for a mail: who sent it, its subject and its text, cut at MAX_MESSAGE, with what it
 * carried beside the text. The forwarded copy keeps the whole mail. */
export function mailText(from: string, subject: string, text: string, attachments: number): string {
  const { text: kept, cut } = head(text.trim(), MAX_MESSAGE);
  const lines = ['Mail to contact@glyphatlas.org', `From: ${from}`, `Subject: ${subject || '(none)'}`];
  if (attachments) lines.push(`Attachments: ${attachments}`);
  return `${lines.join('\n')}\n\n${kept ? kept + (cut ? '\n[…]' : '') : '(no text)'}`;
}

/** A mail to the contact address: forwarded first, so it arrives even if the inbox post fails, then posted.
 * A large mail is not parsed: its post names the sender and subject from the headers and points to the
 * forwarded copy. */
export async function receiveMail(message: ForwardableEmailMessage, env: MailEnv): Promise<void> {
  if (env.CONTACT_FORWARD_TO) await message.forward(env.CONTACT_FORWARD_TO);
  else console.error(JSON.stringify({ event: 'mail_forward_unset' }));
  if (message.rawSize > MAX_PARSED_MAIL) {
    const from = message.headers.get('from') ?? message.from;
    const megabytes = (message.rawSize / 1024 / 1024).toFixed(1);
    await postToInbox(env, mailText(from, message.headers.get('subject') ?? '', `(${megabytes} MB; read it in the forwarded copy)`, 0));
    return;
  }
  const mail = await PostalMime.parse(message.raw);
  const from = mail.from ? [mail.from.name, mail.from.address && `<${mail.from.address}>`].filter(Boolean).join(' ') : message.from;
  const text = mail.text?.trim() ? mail.text : mail.html ? htmlText(mail.html) : '';
  // A mail that cannot be judged is posted in full.
  const odds = await spamOdds(env.AI, mail.from?.address ?? message.from, { from, subject: mail.subject ?? '', text });
  if (odds !== null && odds >= SPAM_THRESHOLD) await postToInbox(env, heldText(from, mail.subject ?? '', odds));
  else await postToInbox(env, mailText(from, mail.subject ?? '', text, mail.attachments.length));
}
