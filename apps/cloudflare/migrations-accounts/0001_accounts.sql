-- The accounts database, glyph-atlas-accounts (decision record 0006). The tables Better Auth reads, as
-- the old database's migrations left them; the cutover copy (scripts/atlas3/accounts.py) creates them
-- with their rows, so every statement here leaves an existing table alone.
CREATE TABLE IF NOT EXISTS "user" ("id" text not null primary key, "name" text not null, "email" text not null unique, "emailVerified" integer not null, "image" text, "createdAt" date not null, "updatedAt" date not null, "isAnonymous" integer, "lastLoginMethod" text, "role" text, "banned" integer default 0, "banReason" text, "banExpires" date);
CREATE TABLE IF NOT EXISTS "session" ("id" text not null primary key, "expiresAt" date not null, "token" text not null unique, "createdAt" date not null, "updatedAt" date not null, "ipAddress" text, "userAgent" text, "userId" text not null references "user" ("id") on delete cascade, "impersonatedBy" text);
CREATE TABLE IF NOT EXISTS "account" ("id" text not null primary key, "accountId" text not null, "providerId" text not null, "userId" text not null references "user" ("id") on delete cascade, "accessToken" text, "refreshToken" text, "idToken" text, "accessTokenExpiresAt" date, "refreshTokenExpiresAt" date, "scope" text, "password" text, "createdAt" date not null, "updatedAt" date not null);
CREATE TABLE IF NOT EXISTS "verification" ("id" text not null primary key, "identifier" text not null, "value" text not null, "expiresAt" date not null, "createdAt" date not null, "updatedAt" date not null);
CREATE TABLE IF NOT EXISTS "rateLimit" ("id" text not null primary key, "key" text not null unique, "count" integer not null, "lastRequest" bigint not null);
CREATE TABLE IF NOT EXISTS "passkey" ("id" text not null primary key, "name" text, "publicKey" text not null, "userId" text not null references "user" ("id") on delete cascade, "credentialID" text not null, "counter" integer not null, "deviceType" text not null, "backedUp" integer not null, "transports" text, "createdAt" date, "aaguid" text);
CREATE INDEX IF NOT EXISTS "session_userId_idx" ON "session" ("userId");
CREATE INDEX IF NOT EXISTS "account_userId_idx" ON "account" ("userId");
CREATE INDEX IF NOT EXISTS "verification_identifier_idx" ON "verification" ("identifier");
CREATE INDEX IF NOT EXISTS "passkey_userId_idx" ON "passkey" ("userId");
CREATE INDEX IF NOT EXISTS "passkey_credentialID_idx" ON "passkey" ("credentialID");
-- Who each connected sign-in account is at its provider, read when it is connected or signed in with.
CREATE TABLE IF NOT EXISTS account_profiles (
  account TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  handle TEXT,
  name TEXT,
  email TEXT,
  image TEXT,
  at TEXT NOT NULL
);

-- A deleted user whose favourites and actor links in glyph-atlas-v3 are still to be removed. The row
-- is written before the user's rows here are deleted and removed once glyph-atlas-v3 is clear, so a
-- deletion that stops between the two databases is finished by the next one.
CREATE TABLE IF NOT EXISTS forgotten (user TEXT PRIMARY KEY, at TEXT NOT NULL) WITHOUT ROWID;
