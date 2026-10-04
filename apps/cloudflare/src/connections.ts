// Who a connected sign-in account is at its provider. The provider's own token is fresh at the moment
// of connecting or signing in, so the profile is read then and kept; an account connected before this
// record existed is filled in from what the site still holds.
type Profile = { handle: string | null; name: string | null; email: string | null; image: string | null };
type AccountRow = { id: string; providerId: string; accountId: string; accessToken: string | null; idToken: string | null; createdAt: string };

const AGENT = { 'user-agent': 'glyphatlas.org' };
const text = (value: unknown) => (typeof value === 'string' && value ? value : null);

/** The claims of an ID token the provider handed over; its signature was checked when it was received. */
function claims(token: string | null): Record<string, unknown> | null {
  const part = token?.split('.')[1];
  if (!part) return null;
  try { return JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/'))) } catch { return null }
}

async function json(url: string, token?: string | null): Promise<Record<string, any> | null> {
  const response = await fetch(url, { headers: { ...AGENT, accept: 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) } })
    .catch(() => null);
  return response?.ok ? response.json() : null;
}

async function read(row: AccountRow): Promise<Profile | null> {
  const id = claims(row.idToken);
  switch (row.providerId) {
    case 'google':
    case 'line':
      // Google serves its picture at 96 pixels unless the address asks for more.
      return id ? { handle: null, name: text(id.name), email: text(id.email), image: text(id.picture)?.replace(/=s\d+-c$/, '=s256-c') ?? null } : null;
    case 'github': {
      // The public profile by account number serves an account whose token has lapsed.
      const user = (row.accessToken && await json('https://api.github.com/user', row.accessToken))
        ?? await json(`https://api.github.com/user/${encodeURIComponent(row.accountId)}`);
      return user ? { handle: text(user.login), name: text(user.name), email: text(user.email), image: text(user.avatar_url) } : null;
    }
    case 'discord': {
      const user = row.accessToken ? await json('https://discord.com/api/users/@me', row.accessToken) : null;
      return user ? { handle: text(user.username), name: text(user.global_name), email: text(user.email),
        image: user.avatar ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png` : null } : null;
    }
    case 'kakao': {
      const user = row.accessToken ? await json('https://kapi.kakao.com/v2/user/me', row.accessToken) : null;
      return user ? { handle: null, name: text(user.properties?.nickname), email: text(user.kakao_account?.email),
        image: text(user.properties?.profile_image) } : null;
    }
  }
  return null;
}

async function save(env: Env, row: AccountRow, profile: Profile) {
  await env.DB.prepare(`INSERT INTO account_profiles(account,provider,handle,name,email,image,at) VALUES(?,?,?,?,?,?,?)
    ON CONFLICT(account) DO UPDATE SET handle=excluded.handle,name=excluded.name,email=excluded.email,image=excluded.image,at=excluded.at`)
    .bind(row.id, row.providerId, profile.handle, profile.name, profile.email, profile.image, new Date().toISOString()).run();
}

/** Read and keep the profile of the account row `id`, right after it was connected or signed in with. */
export async function recordProfile(env: Env, id: string) {
  const row = await env.DB.prepare('SELECT id,providerId,accountId,accessToken,idToken,createdAt FROM account WHERE id=?').bind(id).first<AccountRow>();
  if (!row || row.providerId === 'credential') return;
  const profile = await read(row);
  if (!profile) return;
  const before = await env.DB.prepare('SELECT image FROM account_profiles WHERE account=?').bind(id).first<{ image: string | null }>();
  await save(env, row, profile);
  // A user showing this account's picture keeps showing it after they change it there.
  if (before?.image && profile.image && before.image !== profile.image)
    await env.DB.prepare('UPDATE "user" SET image=? WHERE id=(SELECT userId FROM account WHERE id=?) AND image=?').bind(profile.image, id, before.image).run();
}

/** A user's connected accounts, each with who it is at its provider and when it was connected. */
export async function connections(env: Env, user: string) {
  const rows = (await env.DB.prepare(`SELECT a.id,a.providerId,a.accountId,a.accessToken,a.idToken,a.createdAt,p.handle,p.name,p.email,p.image
    FROM account a LEFT JOIN account_profiles p ON p.account=a.id WHERE a.userId=? AND a.providerId!='credential' ORDER BY a.createdAt`).bind(user).all<AccountRow & Profile & { at?: string }>()).results;
  const out = [];
  for (const row of rows) {
    let profile: Profile | null = row.handle || row.name || row.email ? row : null;
    if (!profile) { profile = await read(row); if (profile) await save(env, row, profile) }
    out.push({ id: row.id, provider: row.providerId, connected: row.createdAt,
      handle: profile?.handle ?? null, name: profile?.name ?? null, email: profile?.email ?? null, image: profile?.image ?? null });
  }
  return { items: out };
}
