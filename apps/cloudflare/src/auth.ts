// Accounts. Every write to the journal is made by a user: an anonymous one the browser starts on its
// first write, which keeps working as it is or is later joined to an account the reader signs in with.
// The journal stays append-only. Its `actor` column holds whatever id wrote the row, and `actors` says
// which user each of those ids now belongs to.
import { betterAuth } from 'better-auth';
import { anonymous } from 'better-auth/plugins';

export type Viewer = { id: string; name: string; anonymous: boolean };
type Auth = ReturnType<typeof build>;

const now = () => new Date().toISOString();
// The ids the browser made up before accounts: `reviewer-` and eight hex digits.
export const LEGACY_REVIEWER = /^reviewer-[0-9a-f]{8}$/;

function build(env: Env, origin: string) {
  return betterAuth({
    appName: 'Glyph Atlas',
    baseURL: origin,
    basePath: '/api/auth',
    secret: env.BETTER_AUTH_SECRET,
    database: env.DB,
    emailAndPassword: { enabled: false },
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
    rateLimit: { enabled: true, storage: 'database', window: 60, max: 60 },
    databaseHooks: {
      user: {
        create: {
          // A user writes under their own id first.
          after: async user => {
            await env.DB.prepare("INSERT OR IGNORE INTO actors(actor,user_id,via,at) VALUES(?,?,'account',?)")
              .bind(user.id, user.id, now()).run();
          },
        },
      },
    },
    plugins: [
      anonymous({
        // Named like the ids the browser used to make up, so a reader's rows read the same as before.
        generateName: () => 'reviewer-' + crypto.randomUUID().slice(0, 8),
        // Signing in with an account from an anonymous session brings that session's work along.
        onLinkAccount: async ({ anonymousUser, newUser }) => {
          await env.DB.prepare('UPDATE actors SET user_id=? WHERE user_id=?').bind(newUser.user.id, anonymousUser.user.id).run();
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

/** The user a request is signed in as, or null. */
export async function viewer(env: Env, request: Request): Promise<Viewer | null> {
  const session = await auth(env, new URL(request.url).origin).api.getSession({ headers: request.headers });
  if (!session) return null;
  const user = session.user as typeof session.user & { isAnonymous?: boolean | null };
  return { id: user.id, name: user.name, anonymous: Boolean(user.isAnonymous) };
}

/** The ids a user's rows were written under, as a subquery over `actors`. */
export const owned = (user: string) => `(SELECT actor FROM actors WHERE user_id='${user.replaceAll("'", "''")}')`;

/**
 * Take over a reviewer id this browser made up before accounts. The first user to name an id holds
 * it; an anonymous user also takes its name, so their history keeps reading as it did.
 */
export async function claim(env: Env, user: Viewer, reviewer: string) {
  if (!LEGACY_REVIEWER.test(reviewer)) return { status: 422, body: { detail: 'Not a reviewer id from before accounts.' } };
  await env.DB.prepare("INSERT OR IGNORE INTO actors(actor,user_id,via,at) VALUES(?,?,'legacy',?)").bind(reviewer, user.id, now()).run();
  const holder = await env.DB.prepare('SELECT user_id FROM actors WHERE actor=?').bind(reviewer).first<{ user_id: string }>();
  if (holder?.user_id !== user.id) return { status: 409, body: { detail: 'Another account already holds this reviewer id.' } };
  if (user.anonymous) await env.DB.prepare('UPDATE "user" SET name=? WHERE id=?').bind(reviewer, user.id).run();
  return { status: 200, body: { reviewer, user: user.id } };
}
