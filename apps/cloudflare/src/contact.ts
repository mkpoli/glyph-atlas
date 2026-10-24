// Messages from the contact form and mail to the contact address. Nothing is stored here: each one is posted
// to the maintainers' inbox room on the community's Matrix server, as the inbox account whose token is
// MATRIX_INBOX_TOKEN. A form sender's network address only keys the rate limit; a mail is also forwarded
// to the address in the secret CONTACT_FORWARD_TO.
import PostalMime from 'postal-mime';
import { Problem } from './busy';
import { body, text } from './input';
import { addressKey } from './reverse';

export const MAX_MESSAGE = 4000;
export const MAX_REPLY = 200;

type InboxEnv = Pick<Env, 'MATRIX_HOMESERVER' | 'MATRIX_INBOX_ROOM'> & { MATRIX_INBOX_TOKEN?: string };
type ContactEnv = InboxEnv & Pick<Env, 'CONTACT'>;
type MailEnv = InboxEnv & { CONTACT_FORWARD_TO?: string };

/** Post one message to the inbox room; false when the homeserver did not take it. Without a token (a local
 * Worker) the message is written to the log instead. */
export async function postToInbox(env: InboxEnv, post: string): Promise<boolean> {
  if (!env.MATRIX_INBOX_TOKEN) { console.log(JSON.stringify({ event: 'inbox_message', post })); return true }
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
  if (!await postToInbox(env, inboxText(message, reply, locale))) throw new Problem(502, 'The message could not be delivered. Try again later.');
  return Response.json({ sent: true });
}

/** The inbox post for a mail: who sent it, its subject and its text, cut at MAX_MESSAGE, with what it
 * carried beside the text. The forwarded copy keeps the whole mail. */
export function mailText(from: string, subject: string, text: string, attachments: number): string {
  const body = text.trim();
  const cut = [...body].length > MAX_MESSAGE ? [...body].slice(0, MAX_MESSAGE).join('') + '\n[…]' : body;
  const head = ['Mail to contact@glyphatlas.org', `From: ${from}`, `Subject: ${subject || '(none)'}`];
  if (attachments) head.push(`Attachments: ${attachments}`);
  return `${head.join('\n')}\n\n${cut || '(no text)'}`;
}

/** A mail to the contact address: forwarded first, so it arrives even if the inbox post fails, then posted. */
export async function receiveMail(message: ForwardableEmailMessage, env: MailEnv): Promise<void> {
  if (env.CONTACT_FORWARD_TO) await message.forward(env.CONTACT_FORWARD_TO);
  const mail = await PostalMime.parse(message.raw);
  const from = mail.from ? [mail.from.name, mail.from.address && `<${mail.from.address}>`].filter(Boolean).join(' ') : message.from;
  await postToInbox(env, mailText(from, mail.subject ?? '', mail.text ?? '', mail.attachments.length));
}
