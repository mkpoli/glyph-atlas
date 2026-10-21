-- The admin's list of accounts with no saved work, newest first, read a page at a time.
CREATE INDEX IF NOT EXISTS user_created ON "user"(createdAt, id);
