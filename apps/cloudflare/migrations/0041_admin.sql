-- Roles and bans (compiled from Better Auth's admin schema), and the submissions an admin rejected.
ALTER TABLE "user" ADD COLUMN "role" text;
ALTER TABLE "user" ADD COLUMN "banned" integer default 0;
ALTER TABLE "user" ADD COLUMN "banReason" text;
ALTER TABLE "user" ADD COLUMN "banExpires" date;
ALTER TABLE "session" ADD COLUMN "impersonatedBy" text;

-- A rejection is an undo an admin made of someone else's submission; the undo's own events are in
-- `events` as any undo's are, and this says who rejected it and why.
CREATE TABLE IF NOT EXISTS rejections (
  submission TEXT PRIMARY KEY REFERENCES submissions(id),
  by TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS submission_actor ON submissions(actor, at);
