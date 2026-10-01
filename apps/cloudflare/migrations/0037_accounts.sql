-- Accounts, in the tables Better Auth reads (compiled from its schema with the anonymous plugin).
CREATE TABLE IF NOT EXISTS "user" ("id" text not null primary key, "name" text not null, "email" text not null unique, "emailVerified" integer not null, "image" text, "createdAt" date not null, "updatedAt" date not null, "isAnonymous" integer);
CREATE TABLE IF NOT EXISTS "session" ("id" text not null primary key, "expiresAt" date not null, "token" text not null unique, "createdAt" date not null, "updatedAt" date not null, "ipAddress" text, "userAgent" text, "userId" text not null references "user" ("id") on delete cascade);
CREATE TABLE IF NOT EXISTS "account" ("id" text not null primary key, "accountId" text not null, "providerId" text not null, "userId" text not null references "user" ("id") on delete cascade, "accessToken" text, "refreshToken" text, "idToken" text, "accessTokenExpiresAt" date, "refreshTokenExpiresAt" date, "scope" text, "password" text, "createdAt" date not null, "updatedAt" date not null);
CREATE TABLE IF NOT EXISTS "verification" ("id" text not null primary key, "identifier" text not null, "value" text not null, "expiresAt" date not null, "createdAt" date not null, "updatedAt" date not null);
CREATE TABLE IF NOT EXISTS "rateLimit" ("id" text not null primary key, "key" text not null unique, "count" integer not null, "lastRequest" bigint not null);
CREATE INDEX IF NOT EXISTS "session_userId_idx" ON "session" ("userId");
CREATE INDEX IF NOT EXISTS "account_userId_idx" ON "account" ("userId");
CREATE INDEX IF NOT EXISTS "verification_identifier_idx" ON "verification" ("identifier");

-- Which user each journal actor belongs to. A user's own id is theirs from the start; a reviewer id a
-- browser made up before accounts is theirs once an admin grants their claim; an anonymous user's ids
-- move to the account they sign in with. The journal itself is never rewritten.
CREATE TABLE IF NOT EXISTS actors (
  actor TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  via TEXT NOT NULL CHECK (via IN ('account','legacy')),
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS actor_user ON actors(user_id);
-- A user asking for a reviewer id from before accounts; an admin gives it to one of them.
CREATE TABLE IF NOT EXISTS actor_claims (
  actor TEXT NOT NULL,
  user_id TEXT NOT NULL,
  at TEXT NOT NULL,
  PRIMARY KEY (actor, user_id)
);
CREATE INDEX IF NOT EXISTS actor_claim_user ON actor_claims(user_id);
