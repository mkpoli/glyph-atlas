// Messages from the contact form. Nothing is stored here: each one is posted to the maintainers' inbox room
// on the community's Matrix server, as the inbox account whose token is MATRIX_INBOX_TOKEN. The sender's
// network address only keys the rate limit.
import { Problem } from './busy';
import { body, text } from './input';
import { addressKey } from './reverse';

export const MAX_MESSAGE = 4000;
export const MAX_REPLY = 200;

type ContactEnv = Pick<Env, 'CONTACT' | 'MATRIX_HOMESERVER' | 'MATRIX_INBOX_ROOM'> & { MATRIX_INBOX_TOKEN?: string };

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
  const post = inboxText(message, reply, locale);
  // Without a token (a local Worker) the message is written to the log instead.
  if (!env.MATRIX_INBOX_TOKEN) { console.log(JSON.stringify({ event: 'contact_message', post })); return Response.json({ sent: true }) }
  const room = encodeURIComponent(env.MATRIX_INBOX_ROOM);
  const sent = await fetch(`${env.MATRIX_HOMESERVER}/_matrix/client/v3/rooms/${room}/send/m.room.message/${crypto.randomUUID()}`, {
    method: 'PUT', headers: { authorization: `Bearer ${env.MATRIX_INBOX_TOKEN}`, 'content-type': 'application/json' },
    body: JSON.stringify({ msgtype: 'm.text', body: post }),
  }).catch(() => null);
  if (!sent?.ok) {
    console.error(JSON.stringify({ event: 'contact_failed', status: sent?.status ?? null }));
    throw new Problem(502, 'The message could not be delivered. Try again later.');
  }
  return Response.json({ sent: true });
}
