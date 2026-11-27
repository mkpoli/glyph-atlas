import { afterEach, describe, expect, it } from 'bun:test';
import { communityCounts, counts, discordMembers, mergeCounts, telegramMembers } from './community';

const realFetch = globalThis.fetch;
const realCaches = globalThis.caches;
afterEach(() => {
  globalThis.fetch = realFetch;
  globalThis.caches = realCaches;
});
const env = (token: string | undefined = 'secret') => ({
  MATRIX_HOMESERVER: 'https://matrix.example' as const, MATRIX_SPACE: '!space:example' as const, MATRIX_INBOX_TOKEN: token,
}) as Parameters<typeof counts>[0];
const answering = (routes: Record<string, () => Response>) => {
  globalThis.fetch = (async (url: string) => {
    const route = Object.keys(routes).find(prefix => String(url).startsWith(prefix));
    return route ? routes[route]() : new Response('', { status: 404 });
  }) as typeof fetch;
};

describe('member counts', () => {
  it('leave out the bridge bots and the accounts that serve the bridges', async () => {
    answering({
      'https://discord.com/api/v10/invites/': () => Response.json({ approximate_member_count: 3 }),
      'https://t.me/': () => new Response('<div class="tgme_page_extra">4 members, 3 online</div>'),
      'https://matrix.example/_matrix/client/v3/rooms/!space%3Aexample/joined_members': () => Response.json({ joined: {
        '@mkpoli:glyphatlas.org': {}, '@reader:matrix.org': {}, '@ops:glyphatlas.org': {}, '@inbox:glyphatlas.org': {},
        '@idsbot:glyphatlas.org': {}, '@bigfatfish:glyphatlas.org': {}, '@qqbot:glyphatlas.org': {},
        '@discord_1089404483068506122:glyphatlas.org': {}, '@telegram_8954644733:glyphatlas.org': {},
        '@qq_67e8e87d21ec9515f10ad6c28f97c320:glyphatlas.org': {}, '@irc_libera_mkpoli:glyphatlas.org': {},
        '@ops:matrix.org': {},
      } }),
    });
    expect(await counts(env())).toEqual({ matrix: 3, discord: 2, telegram: 3 });
  });

  it('fall back to the Discord HTML invite page when the JSON invite API refuses the request', async () => {
    answering({
      'https://discord.com/api/v10/invites/': () => new Response('rate limited', { status: 429 }),
      'https://discord.com/invite/': () => new Response('<meta name="description" content="Check out Glyph Atlas on Discord - hang out with 24 other members and enjoy free voice and text chat." />'),
      'https://t.me/': () => new Response('<div class="tgme_page_extra">11 members, 2 online</div>'),
    });
    expect(await counts(env(undefined))).toEqual({ matrix: null, discord: 23, telegram: 10 });
  });

  it('are null for a place that cannot be read', async () => {
    answering({});
    expect(await counts(env())).toEqual({ matrix: null, discord: null, telegram: null });
    expect((await counts(env(undefined))).matrix).toBeNull();
  });

  it('are null when a platform answers with a page that is not JSON', async () => {
    answering({ '': () => new Response('<html>busy</html>') });
    expect(await counts(env())).toEqual({ matrix: null, discord: null, telegram: null });
  });

  it('read Discord invite meta descriptions and Telegram grouped digits', () => {
    expect(discordMembers('<meta property="og:description" content="hang out with 1,234 other members" />')).toBe(1233);
    expect(discordMembers('<meta content="hang out with 24 other members" name="description" />')).toBe(23);
    expect(discordMembers('<meta name="description" content="Join Discord" />')).toBeNull();
    expect(telegramMembers('<div class="tgme_page_extra">1 234 members, 56 online</div>')).toBe(1233);
    expect(telegramMembers('<div class="tgme_page_extra">1 member</div>')).toBe(0);
    expect(telegramMembers('<div class="tgme_page_description">no count</div>')).toBeNull();
  });

  it('preserve last-known-good counts when a single platform fails transiently', () => {
    expect(mergeCounts({ matrix: 7, discord: null, telegram: 10 }, { matrix: 6, discord: 23, telegram: 9 }))
      .toEqual({ matrix: 7, discord: 23, telegram: 10 });
    expect(mergeCounts({ matrix: 0, discord: null, telegram: null }, { matrix: 5, discord: 23 }))
      .toEqual({ matrix: 0, discord: 23, telegram: null });
  });

  it('expires per-platform fallback after 24 hours even while other platforms keep refreshing, and survives concurrent failures', async () => {
    let now = 0;
    const store = new Map<string, { res: Response; expires: number }>();
    globalThis.caches = {
      default: {
        match: async (req: Request) => {
          const row = store.get(req.url);
          return row && row.expires > now ? row.res.clone() : undefined;
        },
        put: async (req: Request, res: Response) => {
          const maxAge = Number(res.headers.get('cache-control')?.match(/max-age=(\d+)/)?.[1] ?? 0);
          store.set(req.url, { res: res.clone(), expires: now + maxAge });
        },
      },
    } as unknown as CacheStorage;
    const pending: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => { pending.push(p); } } as unknown as ExecutionContext;
    const url = new URL('https://glyphatlas.org/api/community');

    answering({
      'https://discord.com/api/v10/invites/': () => Response.json({ approximate_member_count: 24 }),
      'https://t.me/': () => new Response('<div class="tgme_page_extra">11 members</div>'),
      'https://matrix.example/_matrix/client/v3/rooms/!space%3Aexample/joined_members': () => Response.json({ joined: { '@a:glyphatlas.org': {} } }),
    });
    const first = await communityCounts(env(), ctx, url);
    await Promise.all(pending.splice(0));
    expect(await first.json()).toEqual({ matrix: 1, discord: 23, telegram: 10 });
    expect(first.headers.get('cache-control')).toBe('public, max-age=300');

    // 23 hours later: Discord fails, Telegram succeeds -> Discord fallback still valid, retry age is 60s.
    now += 23 * 3600;
    answering({
      'https://t.me/': () => new Response('<div class="tgme_page_extra">12 members</div>'),
      'https://matrix.example/_matrix/client/v3/rooms/!space%3Aexample/joined_members': () => Response.json({ joined: { '@a:glyphatlas.org': {}, '@b:matrix.org': {} } }),
    });
    const second = await communityCounts(env(), ctx, url);
    await Promise.all(pending.splice(0));
    expect(await second.json()).toEqual({ matrix: 2, discord: 23, telegram: 11 });
    expect(second.headers.get('cache-control')).toBe('public, max-age=60');

    // 2 hours later (25h since Discord last succeeded): Discord fallback has expired and becomes null.
    now += 2 * 3600;
    const third = await communityCounts(env(), ctx, url);
    await Promise.all(pending.splice(0));
    expect(await third.json()).toEqual({ matrix: 2, discord: null, telegram: 11 });

    // Concurrent refresh race: slower failed refresh must not erase a concurrent successful Discord count.
    store.clear();
    let release!: () => void;
    const blocked = new Promise<void>(resolve => { release = resolve; });
    let discordCalls = 0;
    globalThis.fetch = (async (input: string) => {
      const target = String(input);
      if (target.includes('discord.com/api/')) {
        if (++discordCalls === 1) return Response.json({ approximate_member_count: 24 });
        await blocked;
        return new Response('', { status: 503 });
      }
      if (target.startsWith('https://t.me/')) return new Response('<div class="tgme_page_extra">11 members</div>');
      return new Response('', { status: 503 });
    }) as typeof fetch;
    const fast = communityCounts(env(undefined), ctx, url);
    const slow = communityCounts(env(undefined), ctx, url);
    expect(await (await fast).json()).toEqual({ matrix: null, discord: 23, telegram: 10 });
    await Promise.all(pending.splice(0));
    release();
    expect(await (await slow).json()).toEqual({ matrix: null, discord: 23, telegram: 10 });
    await Promise.all(pending.splice(0));
    expect(await store.get('https://glyphatlas.org/api/community?known=discord')!.res.clone().json()).toBe(23);
  });
});
