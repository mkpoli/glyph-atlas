-- GET /history browses events(kind IN ('review','undo')) newest first, optionally by actor or by the
-- crop's label. Each index carries the same partial predicate as the queries that use it, so a review
-- or undo row is found without touching any other kind, and the order is served by the index itself.
CREATE INDEX IF NOT EXISTS event_history ON events(at DESC, id DESC) WHERE kind IN ('review','undo');
CREATE INDEX IF NOT EXISTS event_actor_history ON events(actor, at DESC, id DESC) WHERE kind IN ('review','undo');
-- A review's label sits in evidence.label (a round) or evidence.snapshot.character.label (a single
-- correction); evidence is itself a JSON string, so it is parsed once per row. An undo's own event.evidence
-- is a plain string ('undo of <event id>'), not JSON, and json_extract on it would raise "malformed JSON" —
-- the CASE only evaluates the branch for the row's own kind, so an undo's label comes from the row's own
-- snapshot column instead, and a future kind outside ('review','undo') never touches either column.
CREATE INDEX IF NOT EXISTS event_label_history ON events(
  (CASE kind
    WHEN 'review' THEN coalesce(json_extract(json_extract(event,'$.evidence'),'$.label'),
      json_extract(json_extract(event,'$.evidence'),'$.snapshot.character.label'))
    WHEN 'undo' THEN json_extract(snapshot,'$.character.label')
   END),
  at DESC, id DESC
) WHERE kind IN ('review','undo');
