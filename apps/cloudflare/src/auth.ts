// Accounts. Every write to the journal is made by a user: an anonymous one the browser starts on its
// first write, which keeps working as it is or is later joined to an account the reader signs in with.
// The journal stays append-only. Its `actor` column holds whatever id wrote the row, and `actors` says
// which user each of those ids now belongs to. Users, sessions and sign-in accounts are in `ACCOUNTS`;
// `actors` and `favourites` are in `ATLAS` with what was written.
import { betterAuth } from 'better-auth';
import { APIError, createAuthMiddleware } from 'better-auth/api';
import { admin, anonymous, emailOTP, lastLoginMethod } from 'better-auth/plugins';
import { passkey } from '@better-auth/passkey';
import { MAIL, signInMail } from './mail';
import { recordProfile } from './connections';
import { finishForgetting, startForgetting } from './accounts';
import { writesPaused } from './freeze';

export type Viewer = { id: string; name: string; image: string | null; anonymous: boolean; admin: boolean };
type Auth = ReturnType<typeof build>;


// The accounts a reader can sign in with. Each is offered once its app's id and secret are set, as
// `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` for GitHub.
export const PROVIDERS = ['github', 'google', 'discord', 'line', 'kakao'] as const;
type Provider = typeof PROVIDERS[number];
const secrets = (env: Env) => env as unknown as Record<string, string | undefined>;
export function providers(env: Env): Provider[] {
  return PROVIDERS.filter(name => secrets(env)[`${name.toUpperCase()}_CLIENT_ID`] && secrets(env)[`${name.toUpperCase()}_CLIENT_SECRET`]);
}

// The language a mail is written in: the site language the reader chose, else their browser's first,
// else English. Only a language the mail is written in is taken.
export function mailLocale(request: Request | undefined) {
  const chosen = request?.headers.get('cookie')?.match(/(?:^|;\s*)atlas\.locale=([\w-]+)/)?.[1];
  const wanted = [chosen, ...(request?.headers.get('accept-language') ?? '').split(',').map(part => part.split(';')[0].trim())];
  // A browser names Chinese by region (zh-TW), the site by script (zh-Hant).
  const script = (tag: string) => /^zh-(TW|HK|MO|Hant)/i.test(tag) ? 'zh-Hant' : /^zh/i.test(tag) ? 'zh-Hans' : tag.split('-')[0];
  return wanted.flatMap(tag => tag ? [tag, script(tag)] : []).find(tag => Object.hasOwn(MAIL, tag)) ?? 'en';
}
async function sendCode(env: Env, email: string, code: string, request: Request | undefined, origin: string) {
  const host = new URL(origin).host, mail = signInMail(mailLocale(request), code, host);
  // Without a mail binding (a local Worker) the code is written to the log instead.
  if (!env.EMAIL) { console.log(JSON.stringify({ event: 'sign_in_code', email, code })); return }
  await env.EMAIL.send({ to: email, from: { email: env.MAIL_FROM, name: 'Glyph Atlas' }, subject: mail.subject, text: mail.text, html: mail.html });
}
// The ids the browser made up before accounts: `reviewer-` and eight hex digits.
export const LEGACY_REVIEWER = /^reviewer-[0-9a-f]{8}$/;
// A user who gives no name is called `anon-` and six hex digits, a shape no reviewer id had.
const GENERATED = /^anon-[0-9a-f]{6}$/;
const generatedName = () => 'anon-' + crypto.randomUUID().slice(0, 6);
// How many old reviewer ids one user may hold: a browser held one, a reader with a few browsers a few.
// The cap holds when an anonymous user is joined to an account too.
export const CLAIMS = 5;

function build(env: Env, origin: string, paused: boolean) {
  return betterAuth({
    appName: 'Glyph Atlas',
    baseURL: origin,
    basePath: '/api/auth',
    secret: env.BETTER_AUTH_SECRET,
    database: env.ACCOUNTS,
    // Codes by mail, passkeys and other accounts; there are no passwords.
    emailAndPassword: { enabled: false },
    socialProviders: Object.fromEntries(providers(env).map(name => [name, {
      clientId: secrets(env)[`${name.toUpperCase()}_CLIENT_ID`]!, clientSecret: secrets(env)[`${name.toUpperCase()}_CLIENT_SECRET`]!,
    }])),
    // An account elsewhere joins the user with its address only when that provider has verified the
    // address. Any one way in may be removed, since a code can always be sent to the address.
    account: { accountLinking: { enabled: true, allowUnlinkingAll: true } },
    session: {
      expiresIn: 60 * 60 * 24 * 90,
      updateAge: 60 * 60 * 24,
      // A signed copy of the session in a cookie answers most requests without reading D1.
      cookieCache: { enabled: true, maxAge: 5 * 60 },
      // While writes are paused a session is read as it stands: never extended, and an expired one is
      // not deleted on a read.
      ...(paused ? { disableSessionRefresh: true, deferSessionRefresh: true } : {}),
    },
    advanced: {
      ipAddress: { ipAddressHeaders: ['cf-connecting-ip'] },
      // The auth tables come from the migrations. Checking them reads every table's columns once per
      // isolate, which made schema checks the most frequent statement on the database.
      database: { generateId: () => crypto.randomUUID(), validateSchema: false },
    },
    // Paused, the counts are kept in the isolate, since each request would otherwise write one.
    rateLimit: { enabled: true, storage: paused ? 'memory' : 'database', window: 60, max: 60, customRules: {
      // A code costs a mail; an address gets three a minute and a guess at a code ten.
      '/email-otp/send-verification-otp': { window: 60, max: 3 },
      '/sign-in/email-otp': { window: 60, max: 10 },
      // A banned anonymous user can start another session. An address gets thirty an hour, enough for
      // a class behind one network.
      '/sign-in/anonymous': { window: 3600, max: 30 },
    } },
    databaseHooks: {
      // Who an account elsewhere is, read while its token is fresh: on connecting, and on each sign-in.
      account: {
        create: { after: async account => { await recordProfile(env, account.id).catch(() => {}) } },
        update: { after: async account => { if (account?.id) await recordProfile(env, account.id).catch(() => {}) } },
      },
      user: {
        create: {
          // A sign-in that gives no name (a code by mail) is called `anon-…`; nobody new is named like
          // an old reviewer id, since a new user holds none. A picture comes from a connected account
          // or the avatar route, never from the body of a sign-in by mail.
          before: async (user, ctx) => ({ data: { ...user, name: user.name && !LEGACY_REVIEWER.test(user.name) ? user.name : generatedName(),
            ...(ctx?.path === '/sign-in/email-otp' ? { image: null } : {}) } }),
          // A user writes under their own id first.
          after: async user => {
            await env.ATLAS.prepare("INSERT OR IGNORE INTO actors(key,user,via,at) VALUES(?,?,'account',?)")
              .bind(user.id, user.id, Date.now()).run();
          },
        },
        update: {
          before: async (user, ctx) => {
            // A picture is chosen through `/api/account/avatar`, which checks where it comes from; one
            // sent here could name any address, or another reader's upload.
            if (ctx?.path === '/update-user' && user.image !== undefined) throw new APIError('BAD_REQUEST', { message: 'Choose a picture from the account page.' });
            // A name shaped like an old reviewer id is only for the user who holds that id.
            if (typeof user.name !== 'string' || !LEGACY_REVIEWER.test(user.name)) return;
            const id = ctx?.context.session?.user.id;
            const held = id && await env.ATLAS.prepare('SELECT 1 FROM actors WHERE key=? AND user=?').bind(user.name, id).first();
            if (!held) throw new APIError('BAD_REQUEST', { message: 'This name is an old reviewer id another reviewer may hold.' });
          },
        },
        // Better Auth deletes the user's sessions and sign-in accounts, runs `before`, which marks the user
        // and deletes the anonymous users joined to it, then deletes the user. `after` removes their
        // favourites and links to actors in `ATLAS` and their pictures; a deletion that stops before that
        // is finished by the next one or by the hourly `scheduled` run.
        delete: {
          before: async user => { await startForgetting(env, user.id) },
          after: async () => { await finishForgetting(env).catch(error => console.error(JSON.stringify({ event: 'forget_failed', error: String(error) }))) },
        },
      },
    },
    // A code by mail signs in and does nothing else: no passwords to reset, and an address is changed
    // by connecting another account, so every other route that mails a code is off.
    disabledPaths: ['/email-otp/request-password-reset', '/forget-password/email-otp', '/email-otp/reset-password',
      '/email-otp/request-email-change', '/email-otp/change-email'],
    hooks: {
      // The address a sign-in code goes to is held to SIGN_IN_MAIL before a code is made: a refused
      // request leaves the code already sent in place. The address is lower-cased as the plugin does,
      // so only a request that names it exactly counts against it, and the limiter is keyed by its hash.
      before: createAuthMiddleware(async ctx => {
        if (ctx.path !== '/email-otp/send-verification-otp') return;
        if (ctx.body?.type !== 'sign-in') throw new APIError('BAD_REQUEST', { message: 'Invalid OTP type' });
        if (typeof ctx.body.email !== 'string') return;
        const key = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(ctx.body.email.toLowerCase())))]
          .map(b => b.toString(16).padStart(2, '0')).join('');
        const { success } = await env.SIGN_IN_MAIL.limit({ key });
        if (!success) throw new APIError('TOO_MANY_REQUESTS', { message: 'A code was just sent to this address. Wait a minute and try again.' });
      }),
    },
    plugins: [
      emailOTP({
        otpLength: 6, expiresIn: 10 * 60, allowedAttempts: 5, storeOTP: 'hashed',
        sendVerificationOTP: ({ email, otp }, ctx) => sendCode(env, email, otp, ctx?.request, origin),
      }),
      passkey({ rpID: new URL(origin).hostname, rpName: 'Glyph Atlas', origin }),
      // The sign-in form marks the way this browser signed in last.
      lastLoginMethod({ storeInDatabase: true }),
      // Every user may review; an admin can also ban a user and reject what they saved.
      admin({ defaultRole: 'user', adminRoles: ['admin'], bannedUserMessage: 'This account is banned.' }),
      anonymous({
        generateName: generatedName,
        // The user stays after it signs in to an account, and cannot delete itself: its rows in the
        // journal must stay findable by the people who moderate it.
        disableDeleteAnonymousUser: true,
        // Signing in with an account from an anonymous session brings that session's work along.
        onLinkAccount: async ({ anonymousUser, newUser }) => {
          // A sign-in that lands on the anonymous user itself has nothing to bring.
          if (anonymousUser.user.id === newUser.user.id) return;
          // The link is kept, so that deleting the account deletes the anonymous user too.
          await env.ACCOUNTS.prepare('INSERT OR IGNORE INTO anonymous_links(anonymous,user,at) VALUES(?,?,?)')
            .bind(anonymousUser.user.id, newUser.user.id, new Date().toISOString()).run();
          // Its old reviewer ids come along up to the account's CLAIMS, the earliest claimed first; the rest
          // stay with the anonymous user, unclaimable, and are unlinked when the account is deleted.
          // Its stars too; a crop the account had starred already keeps the account's own.
          await env.ATLAS.batch([
            env.ATLAS.prepare(`UPDATE actors SET user=?1 WHERE id IN (SELECT id FROM actors WHERE user=?2 AND via='legacy' ORDER BY at,id
              LIMIT max(0, ?3 - (SELECT count(*) FROM actors WHERE user=?1 AND via='legacy')))`).bind(newUser.user.id, anonymousUser.user.id, CLAIMS),
            env.ATLAS.prepare("UPDATE actors SET user=? WHERE user=? AND via IS NOT 'legacy'").bind(newUser.user.id, anonymousUser.user.id),
            env.ATLAS.prepare('INSERT OR IGNORE INTO favourites(user,crop,at) SELECT ?,crop,at FROM favourites WHERE user=?').bind(newUser.user.id, anonymousUser.user.id),
            env.ATLAS.prepare('DELETE FROM favourites WHERE user=?').bind(anonymousUser.user.id),
          ]);
          // An account made by this sign-in keeps the name the anonymous one wrote under. A name that is
          // an old reviewer id goes with the id, and the anonymous user, left with neither, is renamed.
          if (Date.now() - new Date(newUser.user.createdAt).getTime() < 60_000 && GENERATED.test(newUser.user.name))
            await env.ACCOUNTS.prepare('UPDATE "user" SET name=? WHERE id=?').bind(anonymousUser.user.name, newUser.user.id).run();
          if (LEGACY_REVIEWER.test(anonymousUser.user.name))
            await env.ACCOUNTS.prepare('UPDATE "user" SET name=? WHERE id=?').bind(generatedName(), anonymousUser.user.id).run();
        },
      }),
    ],
  });
}

// One instance per origin, freeze state and isolate: the options are the same for every request.
const built = new Map<string, Auth>();
export function auth(env: Env, origin: string): Auth {
  const paused = writesPaused(env), key = `${paused ? 'paused ' : ''}${origin}`;
  let found = built.get(key);
  if (!found) built.set(key, found = build(env, origin, paused));
  return found;
}

/**
 * The user a request is signed in as, or null. `fresh` reads the session from `ACCOUNTS` past its cached
 * copy, so a ban or a change of role holds at once; writes and the admin's pages read it so.
 */
export async function viewer(env: Env, request: Request, fresh = false): Promise<Viewer | null> {
  const session = await auth(env, new URL(request.url).origin).api.getSession({ headers: request.headers, query: { disableCookieCache: fresh } });
  if (!session) return null;
  const user = session.user as typeof session.user & { isAnonymous?: boolean | null; role?: string | null; banned?: boolean | null };
  if (user.banned) return null;
  return { id: user.id, name: user.name, image: user.image ?? null, anonymous: Boolean(user.isAnonymous), admin: user.role === 'admin' };
}

/** The ids a user's rows were written under, as a subquery over `actors`. */
export const owned = (user: string) => `(SELECT actor FROM actors WHERE user_id='${user.replaceAll("'", "''")}')`;

/**
 * Take over a reviewer id a browser made up before accounts. The browser that holds the id sends it,
 * and the first account to send an id holds it from then on, past that account's deletion.
 */
export async function claim(env: Env, user: Viewer, reviewer: string) {
  if (!LEGACY_REVIEWER.test(reviewer)) return { status: 422, body: { detail: 'Not a reviewer id from before accounts.' } };
  // An actor row with neither user nor `via` is an id that wrote before any account took it, and the
  // first account to send it takes it. One with `via` set was held by an account since deleted, and
  // stays with nobody. The cap is checked in the same statement, so that claims sent at once stop at it.
  await env.ATLAS.prepare(`INSERT INTO actors(key,user,via,at) SELECT ?1,?2,'legacy',?3
    WHERE (SELECT count(*) FROM actors WHERE user=?2 AND via='legacy') < ?4
    ON CONFLICT(key) DO UPDATE SET user=excluded.user,via=excluded.via,at=excluded.at WHERE actors.user IS NULL AND actors.via IS NULL`)
    .bind(reviewer, user.id, Date.now(), CLAIMS).run();
  const found = await env.ATLAS.prepare('SELECT user,via FROM actors WHERE key=?').bind(reviewer).first<{ user: string | null; via: string | null }>();
  if (found?.user === user.id) return { status: 200, body: { reviewer, status: 'held' } };
  if (found?.user || found?.via) return { status: 409, body: { detail: 'Another account already holds this reviewer id.' } };
  return { status: 429, body: { detail: 'This account already holds as many old reviewer ids as one may.' } };
}
