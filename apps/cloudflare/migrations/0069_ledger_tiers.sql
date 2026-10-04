-- The ledger's tiers are attested, observed and derived (data/ledger.json). A claim that a value names
-- a form, made when a reviewer first picks the value, is the reviewer's own: observed. A date the Ainu
-- records' curation states (`source:ainu-records`, cited by its file and entry) is attested. SQLite
-- changes a CHECK only by building the table again, so `assertions` is copied with each row's rowid,
-- which the Worker's ledger listing pages by, and its indexes and the triggers that keep the ledger
-- unchanged are made again as 0048 makes them. The triggers on the evidence, premise and action tables
-- name `assertions`, so they are dropped first and made again after it.
DROP TRIGGER IF EXISTS assertion_kept;
DROP TRIGGER IF EXISTS assertion_kept_delete;
DROP TRIGGER IF EXISTS evidence_kept_delete;
DROP TRIGGER IF EXISTS premise_kept_delete;
DROP TRIGGER IF EXISTS action_kept_delete;
CREATE TABLE assertions_next (
 id TEXT PRIMARY KEY, submission TEXT,
 subject TEXT NOT NULL, predicate TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '', slot TEXT NOT NULL DEFAULT '',
 object TEXT, value TEXT, alternative_set TEXT,
 tier TEXT NOT NULL CHECK (tier IN ('attested','observed','derived')),
 asserted_by TEXT NOT NULL, asserted_at TEXT NOT NULL,
 confidence REAL, confidence_scheme TEXT, method TEXT, run TEXT, legacy TEXT,
 CHECK ((object IS NULL) <> (value IS NULL)),
 CHECK ((confidence IS NULL) = (confidence_scheme IS NULL))
);
INSERT INTO assertions_next(rowid,id,submission,subject,predicate,scope,slot,object,value,alternative_set,tier,
 asserted_by,asserted_at,confidence,confidence_scheme,method,run,legacy)
SELECT rowid,id,submission,subject,predicate,scope,slot,object,value,alternative_set,
 CASE WHEN tier<>'editorial' THEN tier WHEN substr(predicate,1,5)='date_' THEN 'attested' ELSE 'observed' END,
 asserted_by,asserted_at,confidence,confidence_scheme,method,run,legacy
FROM assertions ORDER BY rowid;
DROP TABLE assertions;
ALTER TABLE assertions_next RENAME TO assertions;
CREATE INDEX IF NOT EXISTS assertion_slot ON assertions(subject,predicate,scope,slot);
CREATE INDEX IF NOT EXISTS assertion_object ON assertions(object) WHERE object IS NOT NULL;
CREATE INDEX IF NOT EXISTS assertion_set ON assertions(alternative_set) WHERE alternative_set IS NOT NULL;
CREATE INDEX IF NOT EXISTS assertion_actor ON assertions(asserted_by,asserted_at);
CREATE INDEX IF NOT EXISTS assertion_subject ON assertions(subject,asserted_at,id);
CREATE TRIGGER IF NOT EXISTS assertion_kept BEFORE UPDATE ON assertions
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
CREATE TRIGGER IF NOT EXISTS assertion_kept_delete BEFORE DELETE ON assertions
 WHEN EXISTS (SELECT 1 FROM units WHERE id=OLD.subject)
   OR NOT EXISTS (SELECT 1 FROM assertion_evidence e WHERE e.assertion=OLD.id AND e.kind='crop')
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
CREATE TRIGGER IF NOT EXISTS evidence_kept_delete BEFORE DELETE ON assertion_evidence
 WHEN EXISTS (SELECT 1 FROM assertions WHERE id=OLD.assertion)
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
CREATE TRIGGER IF NOT EXISTS premise_kept_delete BEFORE DELETE ON assertion_premises
 WHEN EXISTS (SELECT 1 FROM assertions WHERE id=OLD.assertion)
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
CREATE TRIGGER IF NOT EXISTS action_kept_delete BEFORE DELETE ON assertion_actions
 WHEN EXISTS (SELECT 1 FROM assertions WHERE id=OLD.assertion)
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
