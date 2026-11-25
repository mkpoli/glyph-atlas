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
// Enough for a look at a room without turning the panel into an archive.
const LIMIT = 6;
// Read enough history to fill `LIMIT` messages after edits, media attachments and bridge echoes are folded.
const FETCH_LIMIT = LIMIT * 5;
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

// Only text-bearing message types belong in a text preview; `m.image` and `m.file` carry bare filenames.
const TEXT_MSGTYPES = new Set(['m.text', 'm.emote', 'm.notice']);

/** One message's text, or null for events that carry none (membership, media, reactions, redactions, notices). */
export function messageText(content: Record<string, unknown>, sender = ''): string | null {
  // An edit carries the new text in `m.new_content`; showing the original would read as a mistake.
  const replacement = content?.['m.new_content'] as Record<string, unknown> | undefined;
  const effective = replacement && typeof replacement.body === 'string' ? replacement : content;
  const msgtype = effective?.msgtype ?? content?.msgtype;
  if (typeof msgtype === 'string' && !TEXT_MSGTYPES.has(msgtype)) return null;
  const body = effective?.body;
  if (typeof body !== 'string') return null;
  const trimmed = body.replace(/\r\n?/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  if (!trimmed) return null;
  // A bridge bot speaks for the bridge, not for a person; its warnings and status lines stay out.
  if (BRIDGE_BOT.test(sender)) return null;
  // A puppet's notice or emote saying someone joined is the bridge announcing a member, not conversation.
  if (PUPPET.test(sender) && (msgtype === 'm.notice' || msgtype === 'm.emote') && JOIN.test(trimmed)) return null;
  return trimmed.length > MAX_BODY ? `${trimmed.slice(0, MAX_BODY).trimEnd()}…` : trimmed;
}

type RawMessageEvent = {
  event_id?: string;
  type?: string;
  sender?: string;
  origin_server_ts?: number;
  content?: Record<string, unknown>;
};

function replacementTarget(content: Record<string, unknown> | undefined): string | null {
  const relates = content?.['m.relates_to'] as { rel_type?: unknown; event_id?: unknown } | undefined;
  return relates?.rel_type === 'm.replace' && typeof relates.event_id === 'string' ? relates.event_id : null;
}

/** The room ID an alias points at, or null when the alias is unknown or the server will not say. */
export async function resolveAlias(env: PreviewEnv, alias: string, signal?: AbortSignal): Promise<string | null> {
  const response = await fetch(`${env.MATRIX_HOMESERVER}/_matrix/client/v3/directory/room/${encodeURIComponent(alias)}`,
    { headers: { authorization: `Bearer ${env.MATRIX_INBOX_TOKEN}` }, signal });
  if (!response.ok) return null;
  const room = (await response.json() as { room_id?: unknown }).room_id;
  return typeof room === 'string' ? room : null;
}

/** The room's recent messages, oldest first, with edits folded and media or bridge echoes left out. */
export async function roomMessages(env: PreviewEnv, alias: string, signal?: AbortSignal): Promise<PreviewMessage[]> {
  // `/messages` reads a room by ID only, so the alias is resolved first; one this account cannot resolve
  // counts as a room with nothing to show.
  const id = await resolveAlias(env, alias, signal);
  if (id === null) return [];
  const response = await fetch(
    `${env.MATRIX_HOMESERVER}/_matrix/client/v3/rooms/${encodeURIComponent(id)}/messages?dir=b&limit=${FETCH_LIMIT}`,
    { headers: { authorization: `Bearer ${env.MATRIX_INBOX_TOKEN}` }, signal },
  );
  if (!response.ok) return [];
  const chunk = (await response.json() as { chunk?: RawMessageEvent[] }).chunk ?? [];
  // Chunk is newest-first: record the latest `m.replace` edit for each target event so edits update their
  // original message in place instead of appearing beside it.
  const replacements = new Map<string, string | null>();
  for (const item of chunk) {
    if (item.type !== 'm.room.message' || typeof item.sender !== 'string') continue;
    const target = replacementTarget(item.content);
    if (target && !replacements.has(target)) {
      replacements.set(target, messageText(item.content ?? {}, item.sender));
    }
  }
  // Walk oldest-first so when a bridge echoes an earlier message with the same text, the original sender wins.
  // Short replies repeated minutes apart stay; identical long messages or echoes within a minute are folded.
  const ECHO_WINDOW_MS = 60_000;
  const LONG_DUPLICATE = 40;
  const messages: PreviewMessage[] = [];
  const lastSeen = new Map<string, number>();
  for (let i = chunk.length - 1; i >= 0; i--) {
    const item = chunk[i];
    if (item.type !== 'm.room.message' || typeof item.sender !== 'string') continue;
    if (replacementTarget(item.content)) continue;
    const text = item.event_id && replacements.has(item.event_id)
      ? replacements.get(item.event_id)!
      : messageText(item.content ?? {}, item.sender);
    if (text === null) continue;
    const at = item.origin_server_ts ?? 0;
    const prev = lastSeen.get(text);
    if (prev !== undefined && (text.length >= LONG_DUPLICATE || Math.abs(at - prev) < ECHO_WINDOW_MS)) continue;
    lastSeen.set(text, at);
    messages.push({ name: senderName(item.sender), body: text, at });
  }
  return messages.slice(-LIMIT);
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
