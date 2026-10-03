-- Who each connected sign-in account is at its provider (a login, a name, an address), read when it is
-- connected or signed in with, so the account page can say which GitHub or Google account it is.
CREATE TABLE IF NOT EXISTS account_profiles (
  account TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  handle TEXT,
  name TEXT,
  email TEXT,
  image TEXT,
  at TEXT NOT NULL
);
