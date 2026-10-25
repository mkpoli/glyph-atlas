import { afterEach, describe, expect, it } from 'bun:test';
import { counts, telegramMembers } from './community';

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch });
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
        '@discord_1089404483068506122:glyphatlas.org': {}, '@telegram_8954644733:glyphatlas.org': {}, '@irc_libera_mkpoli:glyphatlas.org': {},
      } }),
    });
    expect(await counts(env())).toEqual({ matrix: 2, discord: 2, telegram: 3 });
  });

  it('are null for a place that cannot be read', async () => {
    answering({});
    expect(await counts(env())).toEqual({ matrix: null, discord: null, telegram: null });
    expect((await counts(env(undefined))).matrix).toBeNull();
  });

  it('read Telegram’s grouped digits', () => {
    expect(telegramMembers('<div class="tgme_page_extra">1 234 members, 56 online</div>')).toBe(1233);
    expect(telegramMembers('<div class="tgme_page_extra">1 member</div>')).toBe(0);
    expect(telegramMembers('<div class="tgme_page_description">no count</div>')).toBeNull();
  });
});
