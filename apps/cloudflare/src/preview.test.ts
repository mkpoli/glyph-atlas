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

  it('drops non-text media events whose body is only a filename', () => {
    expect(messageText({ msgtype: 'm.image', body: 'yi.png' }, '@discord_1:glyphatlas.org')).toBeNull();
    expect(messageText({ msgtype: 'm.file', body: 'paper.pdf' }, '@reader:matrix.org')).toBeNull();
    expect(messageText({ msgtype: 'm.video', body: 'clip.mp4' }, '@reader:matrix.org')).toBeNull();
    expect(messageText({ msgtype: 'm.audio', body: 'voice.ogg' }, '@reader:matrix.org')).toBeNull();
  });

  it('collapses runs of blank lines while keeping paragraph breaks', () => {
    expect(messageText({ msgtype: 'm.text', body: 'a\r\n\r\n\r\n\r\nb' })).toBe('a\n\nb');
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

  it('keeps the six latest messages a room has', async () => {
    serving(Array.from({ length: 9 }, (_, index) => event('@a:glyphatlas.org', `m${8 - index}`, 2000 - index)));
    const found = await messages();
    expect(found.map(message => message.body)).toEqual(['m3', 'm4', 'm5', 'm6', 'm7', 'm8']);
  });

  it('keeps only the rooms that have something to show', async () => {
    answering({
      [ALIAS]: () => Response.json({ room_id: '!room:example' }),
      [ROOM]: () => Response.json({ chunk: [event('@a:glyphatlas.org', 'hello')] }),
    });
    const rooms = await preview(env);
    expect(rooms.map(room => room.alias)).toEqual(['#welcome:glyphatlas.org']);
  });

  it('folds Matrix edits into the original message instead of showing both', async () => {
    serving([
      {
        event_id: '$edit2', type: 'm.room.message', sender: '@discord_1:glyphatlas.org', origin_server_ts: 3000,
        content: {
          msgtype: 'm.text', body: '* latest',
          'm.new_content': { msgtype: 'm.text', body: 'latest' },
          'm.relates_to': { rel_type: 'm.replace', event_id: '$orig' },
        },
      },
      {
        event_id: '$edit1', type: 'm.room.message', sender: '@discord_1:glyphatlas.org', origin_server_ts: 2000,
        content: {
          msgtype: 'm.text', body: '* middle',
          'm.new_content': { msgtype: 'm.text', body: 'middle' },
          'm.relates_to': { rel_type: 'm.replace', event_id: '$orig' },
        },
      },
      { event_id: '$orig', type: 'm.room.message', sender: '@discord_1:glyphatlas.org', origin_server_ts: 1000, content: { msgtype: 'm.text', body: 'first' } },
    ]);
    expect(await messages()).toEqual([{ name: 'Discord', body: 'latest', at: 1000 }]);
  });

  it('drops bridge echoes with identical text and keeps the original sender', async () => {
    serving([
      event('@discord_1:glyphatlas.org', 'Hello digest', 3000),
      event('@discord_1:glyphatlas.org', 'Hello digest', 2000),
      event('@bigfatfish:glyphatlas.org', 'Hello digest', 1000),
    ]);
    expect(await messages()).toEqual([{ name: 'bigfatfish', body: 'Hello digest', at: 1000 }]);
  });

  it('keeps identical short replies minutes apart while still folding replayed long digests', async () => {
    const digest = 'Hello everyone, Big Fat Fish here with today’s community digest!';
    serving([
      event('@discord_1:glyphatlas.org', digest, 900_000),
      event('@reader:matrix.org', '謝謝', 600_000),
      event('@mkpoli:glyphatlas.org', '謝謝', 1000),
      event('@bigfatfish:glyphatlas.org', digest, 500),
    ]);
    expect(await messages()).toEqual([
      { name: 'bigfatfish', body: digest, at: 500 },
      { name: 'mkpoli', body: '謝謝', at: 1000 },
      { name: 'reader', body: '謝謝', at: 600_000 },
    ]);
  });

  it('ignores replacement events from a different sender than the original message', async () => {
    serving([
      {
        event_id: '$spoof', type: 'm.room.message', sender: '@mallory:matrix.org', origin_server_ts: 3000,
        content: {
          msgtype: 'm.text', body: '* spoofed',
          'm.new_content': { msgtype: 'm.text', body: 'spoofed' },
          'm.relates_to': { rel_type: 'm.replace', event_id: '$orig' },
        },
      },
      {
        event_id: '$edit', type: 'm.room.message', sender: '@alice:matrix.org', origin_server_ts: 2000,
        content: {
          msgtype: 'm.text', body: '* real edit',
          'm.new_content': { msgtype: 'm.text', body: 'real edit' },
          'm.relates_to': { rel_type: 'm.replace', event_id: '$orig' },
        },
      },
      { event_id: '$orig', type: 'm.room.message', sender: '@alice:matrix.org', origin_server_ts: 1000, content: { msgtype: 'm.text', body: 'original' } },
    ]);
    expect(await messages()).toEqual([{ name: 'alice', body: 'real edit', at: 1000 }]);
  });

  it('keeps short replies from distinct native users within a minute and distinct long messages sharing a 400-char prefix', async () => {
    const prefix = '字'.repeat(400);
    serving([
      event('@discord_1:glyphatlas.org', `${prefix}乙`, 4000),
      event('@discord_1:glyphatlas.org', `${prefix}甲`, 3000),
      event('@bob:matrix.org', 'Yes', 2000),
      event('@alice:matrix.org', 'Yes', 1000),
    ]);
    const found = await messages();
    expect(found.map(m => [m.name, m.at])).toEqual([
      ['alice', 1000],
      ['bob', 2000],
      ['Discord', 3000],
      ['Discord', 4000],
    ]);
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
