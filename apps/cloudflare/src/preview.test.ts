import { afterEach, describe, expect, it } from 'bun:test';
import { messageText, preview, resolveAlias, roomMessages, senderName } from './preview';

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch });
const env = { MATRIX_HOMESERVER: 'https://matrix.example', MATRIX_INBOX_TOKEN: 'secret' } as Parameters<typeof preview>[0];
const ALIAS = 'https://matrix.example/_matrix/client/v3/directory/room/%23welcome%3Aglyphatlas.org';
const ROOM = 'https://matrix.example/_matrix/client/v3/rooms/!room%3Aexample/messages';
const answering = (routes: Record<string, () => Response>) => {
  globalThis.fetch = (async (url: string) => {
    const route = Object.keys(routes).find(prefix => String(url).startsWith(prefix));
    return route ? routes[route]() : new Response('', { status: 404 });
  }) as typeof fetch;
};
const event = (sender: string, body: unknown, at = 1000) =>
  ({ type: 'm.room.message', sender, origin_server_ts: at, content: { body } });
// A room read resolves its alias first, so its two routes always travel together.
const serving = (chunk: unknown[], alias: () => Response = () => Response.json({ room_id: '!room:example' })) =>
  answering({ [ALIAS]: alias, [ROOM]: () => Response.json({ chunk }) });
const messages = () => roomMessages(env, '#welcome:glyphatlas.org');

describe('preview message text', () => {
  it('keeps a message’s own text', () => {
    expect(messageText({ body: '你好' })).toBe('你好');
  });

  it('shows an edit’s new text rather than the one it replaced', () => {
    expect(messageText({ body: '* old', 'm.new_content': { body: 'new' } })).toBe('new');
  });

  it('leaves out events with no text, and one that is only whitespace', () => {
    expect(messageText({})).toBeNull();
    expect(messageText({ body: 42 })).toBeNull();
    expect(messageText({ body: '   \n' })).toBeNull();
  });

  it('cuts a message that is too long to show', () => {
    const cut = messageText({ body: '字'.repeat(500) });
    expect(cut).toHaveLength(401);
    expect(cut!.endsWith('…')).toBe(true);
  });
});

describe('preview alias resolution', () => {
  it('gives the room ID an alias points at', async () => {
    answering({ [ALIAS]: () => Response.json({ room_id: '!room:example' }) });
    expect(await resolveAlias(env, '#welcome:glyphatlas.org')).toBe('!room:example');
  });

  it('is null for an alias the server will not resolve', async () => {
    answering({ [ALIAS]: () => Response.json({ errcode: 'M_NOT_FOUND' }, { status: 404 }) });
    expect(await resolveAlias(env, '#gone:glyphatlas.org')).toBeNull();
    answering({});
    expect(await resolveAlias(env, '#gone:glyphatlas.org')).toBeNull();
  });
});

describe('preview room messages', () => {
  it('keeps messages oldest first and drops the events that carry none', async () => {
    serving([
      event('@mkpoli:glyphatlas.org', 'newest', 3000),
      { type: 'm.room.member', sender: '@reader:matrix.org', state_key: '@reader:matrix.org', content: {} },
      event('@reader:matrix.org', 'middle', 2000),
      { type: 'm.reaction', sender: '@reader:matrix.org', content: {} },
      event('@reader:matrix.org', 'oldest', 1000),
    ]);
    const found = await messages();
    expect(found.map(message => message.body)).toEqual(['oldest', 'middle', 'newest']);
    expect(found[0]).toEqual({ name: 'reader', body: 'oldest', at: 1000 });
  });

  it('reads by room ID, never by alias, since that is all /messages takes', async () => {
    const asked: string[] = [];
    globalThis.fetch = (async (url: string) => {
      asked.push(String(url));
      return String(url).includes('/directory/room/')
        ? Response.json({ room_id: '!room:example' })
        : Response.json({ chunk: [event('@a:glyphatlas.org', 'hello')] });
    }) as typeof fetch;
    await messages();
    const read = asked.find(url => url.includes('/messages'));
    expect(read).toBeDefined();
    expect(read).not.toContain('%23welcome');
  });

  it('is empty for a room whose alias does not resolve', async () => {
    answering({});
    expect(await messages()).toEqual([]);
  });

  it('is empty for a room that cannot be read', async () => {
    answering({ [ALIAS]: () => Response.json({ room_id: '!room:example' }) });
    expect(await messages()).toEqual([]);
  });

  it('keeps only the rooms that have something to show', async () => {
    answering({
      [ALIAS]: () => Response.json({ room_id: '!room:example' }),
      [ROOM]: () => Response.json({ chunk: [event('@a:glyphatlas.org', 'hello')] }),
    });
    const rooms = await preview(env);
    expect(rooms.map(room => room.alias)).toEqual(['#welcome:glyphatlas.org']);
  });
});

describe('preview sender names', () => {
  it('names a bridge puppet for its network, not for its Matrix ID', () => {
    expect(senderName('@discord_1089404483068506122:glyphatlas.org')).toBe('Discord');
    expect(senderName('@telegram_780475347:glyphatlas.org')).toBe('Telegram');
    expect(senderName('@qq_67e8e87d21ec9515f10ad6c28f97c320:glyphatlas.org')).toBe('QQ');
    expect(senderName('@irc_libera_mkpoli:glyphatlas.org')).toBe('IRC');
  });

  it('keeps a person’s own name', () => {
    expect(senderName('@mkpoli:glyphatlas.org')).toBe('mkpoli');
    expect(senderName('@reader:matrix.org')).toBe('reader');
  });

  it('shows a bridge’s own bot as the account it is', () => {
    expect(senderName('@discordbot:glyphatlas.org')).toBe('discordbot');
  });
});

describe('preview noise filtering', () => {
  it('keeps a bridge bot’s warnings and status lines out', () => {
    expect(messageText({ body: '⚠️ Your message was not bridged: unsupported media type' }, '@telegrambot:glyphatlas.org')).toBeNull();
    expect(messageText({ body: 'joined the server' }, '@discordbot:glyphatlas.org')).toBeNull();
    expect(messageText({ body: '* joined the server' }, '@discordbot:glyphatlas.org')).toBeNull();
  });

  it('keeps what a person says, even when it reads like a notice', () => {
    expect(messageText({ body: 'joined the server' }, '@reader:matrix.org')).toBe('joined the server');
    expect(messageText({ body: '⚠️ look at this glyph' }, '@mkpoli:glyphatlas.org')).toBe('⚠️ look at this glyph');
  });

  it('leaves a bridge bot out entirely, since it only ever reports on the bridge', () => {
    expect(messageText({ body: 'Here is the link you asked for.' }, '@telegrambot:glyphatlas.org')).toBeNull();
    expect(messageText({ body: 'Bridge started!' }, '@discordbot:glyphatlas.org')).toBeNull();
  });
});

describe('preview join notices', () => {
  it('keeps a member arriving out, whoever the bridge speaks as', () => {
    expect(messageText({ body: 'joined the server', msgtype: 'm.emote' }, '@discord_329529295502573569:glyphatlas.org')).toBeNull();
    expect(messageText({ body: '* joined the server', msgtype: 'm.emote' }, '@discord_1:glyphatlas.org')).toBeNull();
    expect(messageText({ body: 'left the server', msgtype: 'm.emote' }, '@discord_1:glyphatlas.org')).toBeNull();
  });

  it('keeps a status line the bridge sends as a notice out', () => {
    expect(messageText({ body: 'invited @a:glyphatlas.org', msgtype: 'm.notice' }, '@telegrambot:glyphatlas.org')).toBeNull();
  });

  it('keeps what a person says, whatever its type', () => {
    expect(messageText({ body: 'joined the server', msgtype: 'm.text' }, '@reader:matrix.org')).toBe('joined the server');
    expect(messageText({ body: 'joined the server', msgtype: 'm.emote' }, '@reader:matrix.org')).toBe('joined the server');
  });
});
