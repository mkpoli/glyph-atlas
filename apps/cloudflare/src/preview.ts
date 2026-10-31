// A read-only look at the community chat for people who have not signed in. The rooms are world-readable,
// so the inbox account reads their recent messages and the edge keeps the result for a minute; nothing here
// writes, and no account is needed to see it (`apps/review/src/lib/community.js` names the same rooms).
export const PREVIEW_ROOMS = [
  { alias: '#welcome:glyphatlas.org', name: '歡迎 · Welcome' },
  { alias: '#en:glyphatlas.org', name: 'English' },
  { alias: '#zh:glyphatlas.org', name: '中文' },
  { alias: '#ja:glyphatlas.org', name: '日本語' },
  { alias: '#ko:glyphatlas.org', name: '한국어' },
  { alias: '#vi:glyphatlas.org', name: 'Tiếng Việt' },
  { alias: '#questions:glyphatlas.org', name: '詢問 · Questions' },
  { alias: '#curiosities:glyphatlas.org', name: '奇字 · Curiosities' },
  { alias: '#dev:glyphatlas.org', name: '開發 · Dev' },
];
// A room's messages are read one request each; the edge serves every visitor from one read a minute.
const MAX_AGE = 60, RETRY_AGE = 30;
const TIMEOUT = 5000;
// Enough to fill the panel without turning it into an archive.
const LIMIT = 12;
// The longest message body kept; a longer one is cut with an ellipsis.
const MAX_BODY = 400;

// A message as the panel draws it: who said it, what they said, and when. `name` falls back to the local
// part of the sender's Matrix ID when they have set no display name.
export type PreviewMessage = { name: string; body: string; at: number };
export type PreviewRoom = { alias: string; name: string; messages: PreviewMessage[] };

type PreviewEnv = Pick<Env, 'MATRIX_HOMESERVER'> & { MATRIX_INBOX_TOKEN?: string };

// A bridge's puppet is named for the network it speaks for, never by the Matrix ID it carries: a visitor
// reading a preview has no use for `discord_1089404483068506122`.
const PUPPET = /^@(discord|telegram|qq|irc)_[^:]+:glyphatlas\.org$/;
const NETWORK = /^@(discord|telegram|qq|irc)_/;
const NETWORKS: Record<string, string> = { discord: 'Discord', telegram: 'Telegram', qq: 'QQ', irc: 'IRC' };

/** The name to show for a message's sender: a display name, a network for a bridge puppet, or the local part. */
export function senderName(sender: string): string {
  if (PUPPET.test(sender)) return NETWORKS[NETWORK.exec(sender)![1]] ?? 'Chat';
  return sender.replace(/^@/, '').split(':')[0];
}

// A bridge's own notices are not conversation: a preview shows what people said, and a failed upload reads
// as the community being broken. Everything a bridge bot says is left out, and a member arriving is an
// emote the bridges send, never something that person said.
const BRIDGE_BOT = /^@(telegrambot|discordbot|heisenbridge|qqbot|inbox|ops):glyphatlas\.org$/;
const JOIN = /^\*?\s*(joined|left|was invited|invited|kicked|banned)\b/i;

/** One message's text, or null for events that carry none (membership, reactions, redactions, edits, notices). */
export function messageText(content: Record<string, unknown>, sender = ''): string | null {
  const body = content?.body;
  if (typeof body !== 'string') return null;
  // An edit carries the new text in `m.new_content`; showing the original would read as a mistake.
  const replacement = (content['m.new_content'] as Record<string, unknown> | undefined)?.body;
  const text = typeof replacement === 'string' ? replacement : body;
  const trimmed = text.trim();
  if (!trimmed) return null;
  // A bridge bot speaks for the bridge, not for a person; its warnings and status lines stay out.
  if (BRIDGE_BOT.test(sender)) return null;
  // A puppet's notice or emote saying someone joined is the bridge announcing a member, not conversation.
  if (PUPPET.test(sender) && (content.msgtype === 'm.notice' || content.msgtype === 'm.emote') && JOIN.test(trimmed)) return null;
  return trimmed.length > MAX_BODY ? `${trimmed.slice(0, MAX_BODY).trimEnd()}…` : trimmed;
}

/** The room ID an alias points at, or null when the alias is unknown or the server will not say. */
export async function resolveAlias(env: PreviewEnv, alias: string, signal?: AbortSignal): Promise<string | null> {
  const response = await fetch(`${env.MATRIX_HOMESERVER}/_matrix/client/v3/directory/room/${encodeURIComponent(alias)}`,
    { headers: { authorization: `Bearer ${env.MATRIX_INBOX_TOKEN}` }, signal });
  if (!response.ok) return null;
  const room = (await response.json() as { room_id?: unknown }).room_id;
  return typeof room === 'string' ? room : null;
}

/** The room's recent messages, oldest first, with the events that carry no text left out. */
export async function roomMessages(env: PreviewEnv, alias: string, signal?: AbortSignal): Promise<PreviewMessage[]> {
  // `/messages` reads a room by ID only, so the alias is resolved first; one this account cannot resolve
  // counts as a room with nothing to show.
  const id = await resolveAlias(env, alias, signal);
  if (id === null) return [];
  const response = await fetch(
    `${env.MATRIX_HOMESERVER}/_matrix/client/v3/rooms/${encodeURIComponent(id)}/messages?dir=b&limit=${LIMIT * 3}`,
    { headers: { authorization: `Bearer ${env.MATRIX_INBOX_TOKEN}` }, signal },
  );
  if (!response.ok) return [];
  const chunk = (await response.json() as { chunk?: unknown[] }).chunk ?? [];
  const messages: PreviewMessage[] = [];
  for (const event of chunk) {
    const item = event as { type?: string; sender?: string; origin_server_ts?: number; content?: Record<string, unknown> };
    if (item.type !== 'm.room.message' || typeof item.sender !== 'string') continue;
    const text = messageText(item.content ?? {}, item.sender);
    if (text === null) continue;
    messages.push({ name: senderName(item.sender), body: text, at: item.origin_server_ts ?? 0 });
    if (messages.length === LIMIT) break;
  }
  return messages.reverse();
}

export async function preview(env: PreviewEnv): Promise<PreviewRoom[]> {
  const rooms = await Promise.all(PREVIEW_ROOMS.map(async room => ({
    alias: room.alias,
    name: room.name,
    messages: await roomMessages(env, room.alias, AbortSignal.timeout(TIMEOUT)).catch(() => []),
  })));
  return rooms.filter(room => room.messages.length > 0);
}

export async function communityPreview(env: PreviewEnv, ctx: ExecutionContext, url: URL): Promise<Response> {
  if (!env.MATRIX_INBOX_TOKEN) return Response.json([]);
  const key = new Request(`${url.origin}/api/preview`);
  const cached = await caches.default.match(key);
  if (cached) return cached;
  const rooms = await preview(env);
  const age = rooms.length === 0 ? RETRY_AGE : MAX_AGE;
  const response = Response.json(rooms, { headers: { 'cache-control': `public, max-age=${age}` } });
  ctx.waitUntil(caches.default.put(key, response.clone()));
  return response;
}
