// How many people are in each of the community chat's places, for the contact page's chat doors
// (`apps/review/src/lib/community.js` holds the same invite links). Each count is read from the platform
// itself and kept at the edge for five minutes, or one minute when a place could not be read (it is then
// null). IRC keeps no membership, so it has no count.
export const DISCORD_INVITE = 'DR25YeAYSg';
export const TELEGRAM_INVITE = 'https://t.me/+Q4wiIzMijRwzOWE1';
const MAX_AGE = 300, RETRY_AGE = 60;
// A platform that has not answered by then counts as unreadable.
const TIMEOUT = 5000;

// The bridge's own bot is a member of the Discord server and of the Telegram group; it is not a person.
const BRIDGE_BOTS = 1;
// Matrix accounts that serve the bridges and the inbox, never people: the ops account, the bridges' bots
// and the users they puppet for Discord, Telegram and IRC.
const SERVICE = /^@(ops|inbox|heisenbridge|discordbot|telegrambot|discord_\d+|telegram_\d+|irc_[^:]+):glyphatlas\.org$/;

type CommunityEnv = Pick<Env, 'MATRIX_HOMESERVER' | 'MATRIX_SPACE'> & { MATRIX_INBOX_TOKEN?: string };
export type Counts = { matrix: number | null; discord: number | null; telegram: number | null };

const people = (count: number) => Math.max(0, count - BRIDGE_BOTS);
/** A count read within the timeout; anything that goes wrong (no answer, an error page, a body that is not JSON) is null. */
const read = <T>(count: (signal: AbortSignal) => Promise<T | null>) => count(AbortSignal.timeout(TIMEOUT)).catch(() => null);

export async function discordCount(signal?: AbortSignal): Promise<number | null> {
  const response = await fetch(`https://discord.com/api/v10/invites/${DISCORD_INVITE}?with_counts=true`, { signal });
  if (!response.ok) return null;
  const count = (await response.json() as { approximate_member_count?: unknown }).approximate_member_count;
  return typeof count === 'number' ? people(count) : null;
}

/** The invite page says "1 234 members, 56 online"; a group too small to say so has no count. */
export function telegramMembers(html: string): number | null {
  const found = html.match(/tgme_page_extra">\s*([\d\s,.]+?)\s+members?\b/);
  return found ? people(Number(found[1].replace(/[\s,.]/g, ''))) : null;
}

export async function telegramCount(signal?: AbortSignal): Promise<number | null> {
  const response = await fetch(TELEGRAM_INVITE, { signal });
  return response.ok ? telegramMembers(await response.text()) : null;
}

/** People who joined the space, read as the inbox account; joining a room through the space joins it too. */
export async function matrixCount(env: CommunityEnv, signal?: AbortSignal): Promise<number | null> {
  if (!env.MATRIX_INBOX_TOKEN) return null;
  const space = encodeURIComponent(env.MATRIX_SPACE);
  const response = await fetch(`${env.MATRIX_HOMESERVER}/_matrix/client/v3/rooms/${space}/joined_members`, {
    headers: { authorization: `Bearer ${env.MATRIX_INBOX_TOKEN}` }, signal,
  });
  if (!response.ok) return null;
  const joined = (await response.json() as { joined?: Record<string, unknown> }).joined;
  return joined ? Object.keys(joined).filter(user => !SERVICE.test(user)).length : null;
}

export async function counts(env: CommunityEnv): Promise<Counts> {
  const [matrix, discord, telegram] = await Promise.all([read(signal => matrixCount(env, signal)), read(discordCount), read(telegramCount)]);
  return { matrix, discord, telegram };
}

export async function communityCounts(env: CommunityEnv, ctx: ExecutionContext, url: URL): Promise<Response> {
  const key = new Request(`${url.origin}/api/community`);
  const cached = await caches.default.match(key);
  if (cached) return cached;
  const found = await counts(env);
  const age = Object.values(found).includes(null) ? RETRY_AGE : MAX_AGE;
  const response = Response.json(found, { headers: { 'cache-control': `public, max-age=${age}` } });
  ctx.waitUntil(caches.default.put(key, response.clone()));
  return response;
}
