// Accounts. Every write to the journal is made by a user: an anonymous one the browser starts on its
// first write, which keeps working as it is or is later joined to an account the reader signs in with.
// The journal stays append-only. Its `actor` column holds whatever id wrote the row, and `actors` says
// which user each of those ids now belongs to.
import { betterAuth } from 'better-auth';
import { APIError } from 'better-auth/api';
import { admin, anonymous, emailOTP, lastLoginMethod } from 'better-auth/plugins';
import { passkey } from '@better-auth/passkey';
import { MAIL, signInMail } from './mail';
import { recordProfile } from './connections';

export type Viewer = { id: string; name: string; image: string | null; anonymous: boolean; admin: boolean };
type Auth = ReturnType<typeof build>;

const now = () => new Date().toISOString();

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

function build(env: Env, origin: string) {
  return betterAuth({
    appName: 'Glyph Atlas',
    baseURL: origin,
    basePath: '/api/auth',
    secret: env.BETTER_AUTH_SECRET,
    database: env.DB,
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
    },
    advanced: {
      ipAddress: { ipAddressHeaders: ['cf-connecting-ip'] },
      database: { generateId: () => crypto.randomUUID() },
    },
    rateLimit: { enabled: true, storage: 'database', window: 60, max: 60, customRules: {
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
          // an old reviewer id, since a new user holds none.
          before: async user => ({ data: { ...user, name: user.name && !LEGACY_REVIEWER.test(user.name) ? user.name : generatedName() } }),
          // A user writes under their own id first.
          after: async user => {
            await env.DB.prepare("INSERT OR IGNORE INTO actors(actor,user_id,via,at) VALUES(?,?,'account',?)")
              .bind(user.id, user.id, now()).run();
          },
        },
        update: {
          // A name shaped like an old reviewer id is only for the user who holds that id.
          before: async (user, ctx) => {
            if (typeof user.name !== 'string' || !LEGACY_REVIEWER.test(user.name)) return;
            const id = ctx?.context.session?.user.id;
            const held = id && await env.DB.prepare('SELECT 1 FROM actors WHERE actor=? AND user_id=?').bind(user.name, id).first();
            if (!held) throw new APIError('BAD_REQUEST', { message: 'This name is an old reviewer id another reviewer may hold.' });
          },
        },
      },
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
          await env.DB.prepare('UPDATE actors SET user_id=? WHERE user_id=?').bind(newUser.user.id, anonymousUser.user.id).run();
          // An account made by this sign-in keeps the name the anonymous one wrote under. A name that is
          // an old reviewer id goes with the id, and the anonymous user, left with neither, is renamed.
          if (Date.now() - new Date(newUser.user.createdAt).getTime() < 60_000 && GENERATED.test(newUser.user.name))
            await env.DB.prepare('UPDATE "user" SET name=? WHERE id=?').bind(anonymousUser.user.name, newUser.user.id).run();
          if (LEGACY_REVIEWER.test(anonymousUser.user.name))
            await env.DB.prepare('UPDATE "user" SET name=? WHERE id=?').bind(generatedName(), anonymousUser.user.id).run();
        },
      }),
    ],
  });
}

// One instance per origin and isolate: the options are the same for every request.
const built = new Map<string, Auth>();
export function auth(env: Env, origin: string): Auth {
  let found = built.get(origin);
  if (!found) built.set(origin, found = build(env, origin));
  return found;
}

/**
 * The user a request is signed in as, or null. `fresh` reads the session from D1 past its cached
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

// How many old reviewer ids one user may take: a browser held one, a reader with a few browsers a few.
const CLAIMS = 5;
/**
 * Take over a reviewer id a browser made up before accounts. The browser that holds the id sends it,
 * and the first account to send an id holds it from then on.
 */
export async function claim(env: Env, user: Viewer, reviewer: string) {
  if (!LEGACY_REVIEWER.test(reviewer)) return { status: 422, body: { detail: 'Not a reviewer id from before accounts.' } };
  const holder = await env.DB.prepare('SELECT user_id FROM actors WHERE actor=?').bind(reviewer).first<{ user_id: string }>();
  if (holder) return holder.user_id === user.id ? { status: 200, body: { reviewer, status: 'held' } }
    : { status: 409, body: { detail: 'Another account already holds this reviewer id.' } };
  const held = await env.DB.prepare("SELECT count(*) AS n FROM actors WHERE user_id=? AND via='legacy'").bind(user.id).first<{ n: number }>();
  if ((held?.n ?? 0) >= CLAIMS) return { status: 429, body: { detail: 'This account already holds as many old reviewer ids as one may.' } };
  await env.DB.prepare("INSERT OR IGNORE INTO actors(actor,user_id,via,at) VALUES(?,?,'legacy',?)").bind(reviewer, user.id, now()).run();
  const now_held = await env.DB.prepare('SELECT user_id FROM actors WHERE actor=?').bind(reviewer).first<{ user_id: string }>();
  return now_held?.user_id === user.id ? { status: 200, body: { reviewer, status: 'held' } }
    : { status: 409, body: { detail: 'Another account already holds this reviewer id.' } };
}
