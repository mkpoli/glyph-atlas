-- Review history, claims and crop facts moved from the old database at the cutover (decision record
-- 0006). Crops are named by integer id and cut, never by text id; times are Unix milliseconds.

-- Who wrote: the old actor text (`key`) and the account it belongs to in glyph-atlas-accounts.
CREATE TABLE actors(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, user TEXT, via TEXT, at INTEGER);
CREATE INDEX actor_user ON actors(user);

-- One request a reviewer sent: `key` is the client's actor:uuid, so a resent request is recognised;
-- `request` and `response` are kept as sent.
CREATE TABLE submissions(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, actor INTEGER NOT NULL,
  request TEXT NOT NULL, response TEXT NOT NULL, at INTEGER NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
CREATE INDEX submission_actor ON submissions(actor, at);
CREATE INDEX submission_history ON submissions(at, id) WHERE undone = 0;

-- An event's kind (its verdict, or the mark it set) and issue, in `codes`, numbered once here so the
-- Worker and the translation agree. A mark is a crop state, after the three 0001 numbers.
INSERT INTO codes(field, code, key) VALUES ('kind', 0, 'match'), ('kind', 1, 'wrong'), ('kind', 2, 'unsure'),
  ('kind', 3, 'seen'), ('kind', 4, 'hard'),
  ('issue', 0, 'character'), ('issue', 1, 'merged'), ('issue', 2, 'crop'), ('issue', 3, 'blank'),
  ('issue', 4, 'other'), ('issue', 5, 'unclear'), ('issue', 6, 'reading'),
  ('state', 3, 'seen'), ('state', 4, 'hard');
ALTER TABLE events ADD COLUMN issue INTEGER;
-- The characters a reviewer typed for a joined or misread crop, which the history shows.
ALTER TABLE events ADD COLUMN text TEXT CHECK(length(text) <= 64);
-- A skip names its submission, so an undone round stops counting it.
ALTER TABLE skips ADD COLUMN submission INTEGER;
CREATE INDEX event_crop ON events(crop, revision);
CREATE INDEX event_submission ON events(submission);
CREATE INDEX event_history ON events(at, id);

-- Crops a round showed and left unflagged, at the cut they were shown.
CREATE TABLE seen(submission INTEGER NOT NULL, crop INTEGER NOT NULL, cut INTEGER NOT NULL, at INTEGER NOT NULL,
  PRIMARY KEY(submission, crop)) WITHOUT ROWID;
CREATE INDEX seen_crop ON seen(crop);

CREATE TABLE favourites(user TEXT NOT NULL, crop INTEGER NOT NULL, at INTEGER NOT NULL,
  PRIMARY KEY(user, crop)) WITHOUT ROWID;
CREATE INDEX favourite_recent ON favourites(user, at);

-- The classifier's doubt about a crop's label, and the quiz's shape order: publisher-owned.
CREATE TABLE suspects(crop INTEGER PRIMARY KEY, p REAL NOT NULL, reads_as TEXT, label TEXT NOT NULL, box TEXT);
CREATE TABLE shapes(crop INTEGER PRIMARY KEY, shape_order INTEGER NOT NULL);

-- The claim ledger. A claim on a crop names it by `crop` and `cut` beside its `subject`; a claim on a
-- book, form or word keeps its key in `subject`.
CREATE TABLE assertions(id TEXT PRIMARY KEY, submission TEXT, subject TEXT NOT NULL, crop INTEGER, cut INTEGER,
  predicate TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '', slot TEXT NOT NULL DEFAULT '', object TEXT, value TEXT,
  alternative_set TEXT, tier TEXT NOT NULL CHECK (tier IN ('attested','observed','derived')),
  asserted_by TEXT NOT NULL, asserted_at TEXT NOT NULL, confidence REAL, confidence_scheme TEXT, method TEXT,
  run TEXT, legacy TEXT);
CREATE INDEX assertion_slot ON assertions(subject, predicate, scope, slot);
CREATE INDEX assertion_crop ON assertions(crop, predicate) WHERE crop IS NOT NULL;
CREATE INDEX assertion_object ON assertions(object) WHERE object IS NOT NULL;
CREATE INDEX assertion_set ON assertions(alternative_set) WHERE alternative_set IS NOT NULL;
CREATE INDEX assertion_actor ON assertions(asserted_by, asserted_at);
CREATE TABLE assertion_actions(id TEXT PRIMARY KEY, submission TEXT, assertion TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('accept','reject','retract','adjudicate')), actor TEXT NOT NULL,
  at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '');
CREATE INDEX action_assertion ON assertion_actions(assertion, action);
-- Crop evidence names its crop and cut; page and source evidence keep their reference.
CREATE TABLE assertion_evidence(assertion TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('crop','page','source')),
  ref TEXT NOT NULL, crop INTEGER, cut INTEGER, locator TEXT, PRIMARY KEY(assertion, kind, ref)) WITHOUT ROWID;
CREATE INDEX evidence_crop ON assertion_evidence(crop, cut) WHERE crop IS NOT NULL;
CREATE TABLE assertion_premises(assertion TEXT NOT NULL, premise TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'premise',
  PRIMARY KEY(assertion, premise)) WITHOUT ROWID;
CREATE INDEX premise_assertion ON assertion_premises(premise);
CREATE TABLE current_claims(subject TEXT NOT NULL, predicate TEXT NOT NULL, scope TEXT NOT NULL, slot TEXT NOT NULL,
  crop INTEGER, cut INTEGER, status TEXT NOT NULL, object TEXT, value TEXT, members TEXT NOT NULL,
  supporting TEXT NOT NULL, claims TEXT NOT NULL, resolver TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY(subject, predicate, scope, slot)) WITHOUT ROWID;
CREATE INDEX current_claim_crop ON current_claims(crop, predicate) WHERE crop IS NOT NULL;
CREATE INDEX current_claim_object ON current_claims(predicate, object, subject) WHERE object IS NOT NULL;
CREATE TABLE ledger_submissions(id TEXT PRIMARY KEY, actor TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL,
  at TEXT NOT NULL);
CREATE TABLE rejections(submission INTEGER PRIMARY KEY, by TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', at INTEGER NOT NULL);

-- The forms tool: its decisions name corpus glyphs by integer id in `crops`, beside the old `units`.
CREATE TABLE forms(id TEXT PRIMARY KEY, anchor TEXT UNIQUE, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE form_decisions(seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, at TEXT NOT NULL, actor TEXT NOT NULL,
  kind TEXT NOT NULL, family TEXT NOT NULL, form TEXT, cluster TEXT, revision TEXT NOT NULL, units TEXT NOT NULL,
  crops TEXT NOT NULL, note TEXT NOT NULL, issue TEXT, character TEXT, written_family TEXT);
CREATE INDEX form_decision_at ON form_decisions(at);
CREATE INDEX form_decision_cluster ON form_decisions(cluster, at);
CREATE TABLE corpus_follow(crop INTEGER PRIMARY KEY);

-- Two archival rows from an earlier written-form tool.
CREATE TABLE written_forms(id TEXT PRIMARY KEY, submission TEXT NOT NULL UNIQUE, crop INTEGER NOT NULL, actor TEXT NOT NULL,
  revision INTEGER NOT NULL, pixels TEXT NOT NULL, label TEXT NOT NULL, form TEXT, request TEXT NOT NULL, at TEXT NOT NULL);
