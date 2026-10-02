-- Passkeys, and the way each user last signed in (compiled from Better Auth's passkey and
-- last-login-method schemas).
ALTER TABLE "user" ADD COLUMN "lastLoginMethod" text;
CREATE TABLE IF NOT EXISTS "passkey" ("id" text not null primary key, "name" text, "publicKey" text not null, "userId" text not null references "user" ("id") on delete cascade, "credentialID" text not null, "counter" integer not null, "deviceType" text not null, "backedUp" integer not null, "transports" text, "createdAt" date, "aaguid" text);
CREATE INDEX IF NOT EXISTS "passkey_userId_idx" ON "passkey" ("userId");
CREATE INDEX IF NOT EXISTS "passkey_credentialID_idx" ON "passkey" ("credentialID");
