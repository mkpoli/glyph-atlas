CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS characters (
 code_point TEXT PRIMARY KEY, character TEXT NOT NULL, name TEXT NOT NULL,
 data TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS character_text ON characters(character);
CREATE TABLE IF NOT EXISTS aliases (
 query TEXT NOT NULL, code_point TEXT NOT NULL, rank INTEGER NOT NULL,
 PRIMARY KEY(query,code_point)
);
CREATE TABLE IF NOT EXISTS units (
 id TEXT PRIMARY KEY, origin TEXT NOT NULL, character TEXT,
 reading TEXT, family TEXT, visual_group TEXT, production TEXT NOT NULL,
 category TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL,
 quiz INTEGER NOT NULL, priority INTEGER NOT NULL, shuffle INTEGER NOT NULL,
 data TEXT NOT NULL, snapshot TEXT NOT NULL, context TEXT NOT NULL, visual TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS unit_character ON units(origin,character,state);
CREATE INDEX IF NOT EXISTS unit_family ON units(origin,family,visual_group);
CREATE INDEX IF NOT EXISTS unit_sample ON units(origin,shuffle);
CREATE INDEX IF NOT EXISTS unit_review ON units(origin,quiz,production,state);
CREATE TABLE IF NOT EXISTS corpus_units (
 id TEXT PRIMARY KEY, character TEXT, family TEXT, visual_group TEXT,
 shuffle INTEGER NOT NULL, object TEXT NOT NULL, offset INTEGER NOT NULL, size INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS corpus_character ON corpus_units(character,id);
CREATE INDEX IF NOT EXISTS corpus_family ON corpus_units(family,visual_group,id);
CREATE INDEX IF NOT EXISTS corpus_sample ON corpus_units(shuffle);
CREATE TABLE IF NOT EXISTS media (
 key TEXT PRIMARY KEY, object TEXT NOT NULL, offset INTEGER NOT NULL,
 size INTEGER NOT NULL, content_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, request TEXT NOT NULL,
 response TEXT NOT NULL, at TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, submission TEXT NOT NULL REFERENCES submissions(id),
 target TEXT NOT NULL REFERENCES units(id), actor TEXT NOT NULL,
 expected_revision INTEGER NOT NULL, before_data TEXT NOT NULL,
 after_data TEXT NOT NULL, event TEXT NOT NULL, snapshot TEXT NOT NULL,
 kind TEXT NOT NULL, at TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS event_target ON events(target,at);
CREATE INDEX IF NOT EXISTS event_submission ON events(submission);
CREATE TRIGGER IF NOT EXISTS event_revision_guard BEFORE INSERT ON events
BEGIN
 SELECT RAISE(ABORT, 'review_revision_conflict')
 WHERE (SELECT revision FROM units WHERE id=NEW.target) IS NOT NEW.expected_revision;
END;
CREATE TRIGGER IF NOT EXISTS event_apply AFTER INSERT ON events
BEGIN
 UPDATE units SET data=NEW.after_data, revision=NEW.expected_revision+1,
 character=iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),
 reading=json_extract(NEW.after_data,'$.reading'),
 family=coalesce(json_extract(NEW.after_data,'$.grapheme.code_point'),json_extract(NEW.after_data,'$.grapheme'),family),
 visual_group=json_extract(NEW.after_data,'$.visual_group.id'),
 category=coalesce(json_extract(NEW.after_data,'$.category'),category),
 state=json_extract(NEW.after_data,'$.state')
 WHERE id=NEW.target;
END;
