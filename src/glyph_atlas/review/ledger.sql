-- The ledger's tables and the forms and representations its claims name, as the review store and a
-- publication catalogue make them. The site's first database was made with the same statements.

-- The assertion ledger (docs/design/form-model.md): every claim about a crop, a form or a grapheme, who
-- made it, on what evidence, and each acceptance, rejection, retraction and adjudication of it. Rows are
-- never changed. A claim about a crop is removed only once the crop has left the site, which is what
-- taking a withdrawn document down does (`scripts/withdraw_documents.py`), and its evidence, premises
-- and actions only once it is gone; every other row stays. `current_claims` is the ledger resolved slot by slot by the resolver in
-- `data/ledger.json`, written again whenever one of the slot's rows is added. The local review service
-- keeps the same tables in its store, made from this file.
--
-- A write is one submission, keyed by its actor and the id its client chose, so a retry answers with the
-- first response and the same id sent with anything else is refused.
CREATE TABLE IF NOT EXISTS ledger_submissions (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL, at TEXT NOT NULL
);
-- One claim: `subject` `predicate` `object` (an entity id) or `value` (a typed value, as JSON), in
-- `scope` ('' for the Atlas default). `slot` is '' for a predicate with one value per subject and the
-- object or value itself for one with many, so a slot is what one resolution decides. The members of an
-- "F or G" claim share `alternative_set`. `asserted_by` is the journal actor or the source; `legacy`
-- names the journal row a migrated claim came from.
CREATE TABLE IF NOT EXISTS assertions (
 id TEXT PRIMARY KEY, submission TEXT,
 subject TEXT NOT NULL, predicate TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '', slot TEXT NOT NULL DEFAULT '',
 object TEXT, value TEXT, alternative_set TEXT,
 tier TEXT NOT NULL CHECK (tier IN ('attested','observed','derived')),
 asserted_by TEXT NOT NULL, asserted_at TEXT NOT NULL,
 confidence REAL, confidence_scheme TEXT, method TEXT, run TEXT, legacy TEXT,
 CHECK ((object IS NULL) <> (value IS NULL)),
 CHECK ((confidence IS NULL) = (confidence_scheme IS NULL))
);
CREATE INDEX IF NOT EXISTS assertion_slot ON assertions(subject,predicate,scope,slot);
CREATE INDEX IF NOT EXISTS assertion_object ON assertions(object) WHERE object IS NOT NULL;
CREATE INDEX IF NOT EXISTS assertion_set ON assertions(alternative_set) WHERE alternative_set IS NOT NULL;
CREATE INDEX IF NOT EXISTS assertion_actor ON assertions(asserted_by,asserted_at);
CREATE INDEX IF NOT EXISTS assertion_subject ON assertions(subject,asserted_at,id);
-- What a claim rests on. `crop` is the evidence version of the subject crop (0047), and the claim stands
-- only while that is the crop's current version; `page` a page locator; `source` a source row and hash.
CREATE TABLE IF NOT EXISTS assertion_evidence (
 assertion TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('crop','page','source')), ref TEXT NOT NULL,
 locator TEXT, PRIMARY KEY (assertion,kind,ref)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS evidence_ref ON assertion_evidence(kind,ref);
-- The claims a derivation or a carried-forward claim rests on.
CREATE TABLE IF NOT EXISTS assertion_premises (
 assertion TEXT NOT NULL, premise TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'premise',
 PRIMARY KEY (assertion,premise)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS premise_assertion ON assertion_premises(premise);
-- Acceptance is its own row: an accept or reject by anyone but the asserter, a retraction by the
-- asserter, an adjudication by an adjudicator.
CREATE TABLE IF NOT EXISTS assertion_actions (
 id TEXT PRIMARY KEY, submission TEXT, assertion TEXT NOT NULL,
 action TEXT NOT NULL CHECK (action IN ('accept','reject','retract','adjudicate')),
 actor TEXT NOT NULL, at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS action_assertion ON assertion_actions(assertion,action);
CREATE INDEX IF NOT EXISTS action_submission ON assertion_actions(submission);
-- Each slot's current value as the resolver named in `resolver` found it: `status` (asserted, accepted,
-- adjudicated, disputed, rejected), the value when it is one (`object` or `value`), `members` (the value,
-- or each alternative of a set, with its confidence), `supporting` (the ids of the claims it stands on),
-- `claims` (every live claim with its acceptances), and the crop version it was resolved against. A slot
-- with no live claim has no row: for a crop's form, that is unsorted.
CREATE TABLE IF NOT EXISTS current_claims (
 subject TEXT NOT NULL, predicate TEXT NOT NULL, scope TEXT NOT NULL, slot TEXT NOT NULL,
 status TEXT NOT NULL, object TEXT, value TEXT, members TEXT NOT NULL, supporting TEXT NOT NULL,
 claims TEXT NOT NULL, crop_version TEXT, resolver TEXT NOT NULL, at TEXT NOT NULL,
 PRIMARY KEY (subject,predicate,scope,slot)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS current_claim_object ON current_claims(predicate,object,subject) WHERE object IS NOT NULL;
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
CREATE TRIGGER IF NOT EXISTS evidence_kept BEFORE UPDATE ON assertion_evidence
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
CREATE TRIGGER IF NOT EXISTS evidence_kept_delete BEFORE DELETE ON assertion_evidence
 WHEN EXISTS (SELECT 1 FROM assertions WHERE id=OLD.assertion)
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
CREATE TRIGGER IF NOT EXISTS premise_kept BEFORE UPDATE ON assertion_premises
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
CREATE TRIGGER IF NOT EXISTS premise_kept_delete BEFORE DELETE ON assertion_premises
 WHEN EXISTS (SELECT 1 FROM assertions WHERE id=OLD.assertion)
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
CREATE TRIGGER IF NOT EXISTS action_kept BEFORE UPDATE ON assertion_actions
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;
CREATE TRIGGER IF NOT EXISTS action_kept_delete BEFORE DELETE ON assertion_actions
 WHEN EXISTS (SELECT 1 FROM assertions WHERE id=OLD.assertion)
BEGIN
 SELECT RAISE(ABORT,'ledger_immutable');
END;

-- Forms and their representations (docs/design/form-model.md, `glyph_atlas.representation`). A form is
-- a category of written appearance with an id of its own; the claims that a crop is written in it, that
-- a representation names it, and how it relates to other forms are rows of the ledger (0048). A form
-- named by choosing an encoded character, an IVS or a description is that value's broad form:
-- `anchor` is the representation it is the encoded form of, at most one form each, and its id is
-- derived from the representation. Rows are never changed or removed. The local review service keeps
-- the same tables in its store, made from this file.
CREATE TABLE IF NOT EXISTS forms (
 id TEXT PRIMARY KEY, anchor TEXT UNIQUE, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
-- A representation's value is kept exactly as written; its id hashes scheme, namespace, version and
-- value. A private-use code point names something only with the namespace and version of its mapping.
CREATE TABLE IF NOT EXISTS representations (
 id TEXT PRIMARY KEY,
 scheme TEXT NOT NULL CHECK (scheme IN ('unicode','ivs','ids','mj','glyphwiki','pua')),
 value TEXT NOT NULL, namespace TEXT, version TEXT,
 CHECK (scheme<>'pua' OR (namespace IS NOT NULL AND version IS NOT NULL))
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS representation_value ON representations(value);
CREATE TRIGGER IF NOT EXISTS form_kept BEFORE UPDATE ON forms
BEGIN
 SELECT RAISE(ABORT,'form_immutable');
END;
CREATE TRIGGER IF NOT EXISTS form_kept_delete BEFORE DELETE ON forms
BEGIN
 SELECT RAISE(ABORT,'form_immutable');
END;
CREATE TRIGGER IF NOT EXISTS representation_kept BEFORE UPDATE ON representations
BEGIN
 SELECT RAISE(ABORT,'form_immutable');
END;
CREATE TRIGGER IF NOT EXISTS representation_kept_delete BEFORE DELETE ON representations
BEGIN
 SELECT RAISE(ABORT,'form_immutable');
END;
-- Written forms are form claims now. The journal (0038) stays as the record of what was saved and is no
-- longer written; `units.written_form` stays empty, since dropping a column rewrites every row of
-- `units`, more than one D1 statement may take.
DROP TRIGGER IF EXISTS written_form_apply;
DROP TRIGGER IF EXISTS written_form_revision_guard;
