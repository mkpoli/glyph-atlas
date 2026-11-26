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

// A message as the panel draws it: who said it, an opaque per-room author key for grouping without
// exposing bridge puppet IDs, what they said, and when. `name` falls back to the local part of the
// sender's Matrix ID when they have set no display name.
export type PreviewMessage = { name: string; by?: string; body: string; at: number };
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

function rawMessageText(content: Record<string, unknown>, sender = ''): string | null {
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
  return trimmed;
}

function truncateText(text: string): string {
  return text.length > MAX_BODY ? `${text.slice(0, MAX_BODY).trimEnd()}…` : text;
}

/** One message's text, or null for events that carry none (membership, media, reactions, redactions, notices). */
export function messageText(content: Record<string, unknown>, sender = ''): string | null {
  const raw = rawMessageText(content, sender);
  return raw === null ? null : truncateText(raw);
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
  // Record the latest `m.replace` edit keyed by `(sender, target)`: highest `origin_server_ts`, breaking
  // ties by lexicographically larger `event_id`, as required by the Matrix spec.
  const replacements = new Map<string, { ts: number; eventId: string; text: string | null }>();
  for (const item of chunk) {
    if (item.type !== 'm.room.message' || typeof item.sender !== 'string') continue;
    const target = replacementTarget(item.content);
    if (!target) continue;
    const key = `${item.sender}\0${target}`;
    const ts = item.origin_server_ts ?? 0;
    const eventId = item.event_id ?? '';
    const prev = replacements.get(key);
    if (!prev || ts > prev.ts || (ts === prev.ts && eventId > prev.eventId)) {
      replacements.set(key, { ts, eventId, text: rawMessageText(item.content ?? {}, item.sender) });
    }
  }
  // Walk oldest-first so when a bridge echoes an earlier message with the same text, the original sender wins.
  // Deduplicate on the full normalized text before truncation, and only when the duplicate is a bridge puppet
  // echo or repeated by the same sender — two native users giving the same short reply both stay.
  const ECHO_WINDOW_MS = 60_000;
  const LONG_DUPLICATE = 40;
  const messages: PreviewMessage[] = [];
  const lastSeen = new Map<string, { at: number; sender: string }>();
  const authors = new Map<string, string>();
  for (let i = chunk.length - 1; i >= 0; i--) {
    const item = chunk[i];
    if (item.type !== 'm.room.message' || typeof item.sender !== 'string') continue;
    if (replacementTarget(item.content)) continue;
    const replaceKey = item.event_id ? `${item.sender}\0${item.event_id}` : null;
    const raw = replaceKey && replacements.has(replaceKey)
      ? replacements.get(replaceKey)!.text
      : rawMessageText(item.content ?? {}, item.sender);
    if (raw === null) continue;
    const at = item.origin_server_ts ?? 0;
    const prev = lastSeen.get(raw);
    const duplicate = prev !== undefined && (
      (prev.sender === item.sender && (raw.length >= LONG_DUPLICATE || Math.abs(at - prev.at) < ECHO_WINDOW_MS))
      || (PUPPET.test(item.sender) && (raw.length >= LONG_DUPLICATE || (!PUPPET.test(prev.sender) && Math.abs(at - prev.at) < 15_000)))
    );
    if (duplicate) continue;
    lastSeen.set(raw, { at, sender: item.sender });
    let by = authors.get(item.sender);
    if (!by) {
      by = `s${authors.size + 1}`;
      authors.set(item.sender, by);
    }
    messages.push({ name: senderName(item.sender), by, body: truncateText(raw), at });
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
