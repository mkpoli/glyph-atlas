// How many people are in each of the community chat's places, for the contact page's chat doors
// (`apps/review/src/lib/community.js` holds the same invite links). Each count is read from the platform
// itself and kept at the edge for five minutes, or one minute when a place could not be read (it is then
// null). IRC keeps no membership, so it has no count.
export const DISCORD_INVITE = 'DR25YeAYSg';
export const TELEGRAM_INVITE = 'https://t.me/+Q4wiIzMijRwzOWE1';
const MAX_AGE = 300, RETRY_AGE = 60, KNOWN_AGE = 86400;
// A platform that has not answered by then counts as unreadable.
const TIMEOUT = 5000;
const USER_AGENT = 'GlyphAtlas (+https://glyphatlas.org)';

// The bridge's own bot is a member of the Discord server and of the Telegram group; it is not a person.
const BRIDGE_BOTS = 1;
// Matrix accounts that serve the community rather than speak for a person: ops, the inbox, the IDS
// and whale bots, the bridges' bots and the users they puppet for Discord, Telegram, QQ and IRC.
const SERVICE = /^@(ops|inbox|idsbot|bigfatfish|heisenbridge|discordbot|telegrambot|qqbot|discord_\d+|telegram_\d+|qq_[^:]+|irc_[^:]+):glyphatlas\.org$/;

type CommunityEnv = Pick<Env, 'MATRIX_HOMESERVER' | 'MATRIX_SPACE'> & { MATRIX_INBOX_TOKEN?: string };
export type Counts = { matrix: number | null; discord: number | null; telegram: number | null };

const people = (count: number) => Math.max(0, count - BRIDGE_BOTS);
/** A count read within the timeout; anything that goes wrong (no answer, an error page, a body that is not JSON) is null. */
const read = <T>(count: (signal: AbortSignal) => Promise<T | null>) => count(AbortSignal.timeout(TIMEOUT)).catch(() => null);

/** The invite page's meta description says "hang out with 24 other members". */
export function discordMembers(html: string): number | null {
  const meta = html.match(/<meta[^>]+(?:name|property)=["'](?:og:|twitter:)?description["'][^>]+content=["']([^"']+)["']/i)
    ?? html.match(/<meta[^>]+content=["']([^"']+)["'][^>]+(?:name|property)=["'](?:og:|twitter:)?description["']/i);
  const found = meta?.[1].match(/([\d\s,.]+?)\s+(?:other\s+)?members?\b/i);
  if (!found) return null;
  const parsed = Number(found[1].replace(/[\s,.]/g, ''));
  return Number.isFinite(parsed) ? people(parsed) : null;
}

export async function discordCount(signal?: AbortSignal): Promise<number | null> {
  const headers = { 'user-agent': USER_AGENT };
  const api = await fetch(`https://discord.com/api/v10/invites/${DISCORD_INVITE}?with_counts=true`, {
    headers: { ...headers, accept: 'application/json' }, signal,
  }).catch(() => null);
  if (api?.ok) {
    const data = await api.json().catch(() => null) as { approximate_member_count?: unknown } | null;
    if (typeof data?.approximate_member_count === 'number') return people(data.approximate_member_count);
  }
  // When the JSON invite API rate-limits shared Worker IPs, fall back to the public invite page's meta tag.
  const page = await fetch(`https://discord.com/invite/${DISCORD_INVITE}`, { headers, signal }).catch(() => null);
  return page?.ok ? discordMembers(await page.text()) : null;
}

/** The invite page says "1 234 members, 56 online"; a group too small to say so has no count. */
export function telegramMembers(html: string): number | null {
  const found = html.match(/tgme_page_extra">\s*([\d\s,.]+?)\s+members?\b/);
  if (!found) return null;
  const parsed = Number(found[1].replace(/[\s,.]/g, ''));
  return Number.isFinite(parsed) ? people(parsed) : null;
}

export async function telegramCount(signal?: AbortSignal): Promise<number | null> {
  const response = await fetch(TELEGRAM_INVITE, { headers: { 'user-agent': USER_AGENT }, signal });
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

/** Fill platforms that transiently returned null from the last known good counts on this edge PoP. */
export function mergeCounts(fresh: Counts, known: Partial<Counts> | null): Counts {
  return {
    matrix: fresh.matrix ?? (typeof known?.matrix === 'number' ? known.matrix : null),
    discord: fresh.discord ?? (typeof known?.discord === 'number' ? known.discord : null),
    telegram: fresh.telegram ?? (typeof known?.telegram === 'number' ? known.telegram : null),
  };
}

const PLATFORMS = ['matrix', 'discord', 'telegram'] as const;

export async function communityCounts(env: CommunityEnv, ctx: ExecutionContext, url: URL): Promise<Response> {
  const key = new Request(`${url.origin}/api/community`);
  const cached = await caches.default.match(key);
  if (cached) return cached;
  const fresh = await counts(env);
  // Read each platform's last-known-good key AFTER fetching so a slower failed refresh sees any
  // concurrent success, and write back only platforms that succeeded so stale entries always expire.
  const knownKey = (platform: (typeof PLATFORMS)[number]) => new Request(`${url.origin}/api/community?known=${platform}`);
  const knownEntries = await Promise.all(PLATFORMS.map(async platform => {
    if (typeof fresh[platform] === 'number') return [platform, null] as const;
    const hit = await caches.default.match(knownKey(platform));
    const value = hit ? await hit.json().catch(() => null) : null;
    return [platform, typeof value === 'number' ? value : null] as const;
  }));
  const known = Object.fromEntries(knownEntries) as Partial<Counts>;
  const merged = mergeCounts(fresh, known);
  // Keep retry cadence short whenever any live read failed, even when last-known-good fills the response.
  const age = Object.values(fresh).includes(null) ? RETRY_AGE : MAX_AGE;
  const response = Response.json(merged, { headers: { 'cache-control': `public, max-age=${age}` } });
  // Only store the main response if a concurrent full success has not already populated it.
  if (age === MAX_AGE || !(await caches.default.match(key))) {
    ctx.waitUntil(caches.default.put(key, response.clone()));
  }
  for (const platform of PLATFORMS) {
    if (typeof fresh[platform] === 'number') {
      ctx.waitUntil(caches.default.put(knownKey(platform), Response.json(fresh[platform], {
        headers: { 'cache-control': `public, max-age=${KNOWN_AGE}` },
      })));
    }
  }
  return response;
}
