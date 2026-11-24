-- The saves that arrange 構形, as the history lists them, newest first: everyone's, one reader's, and those
-- of one form. A filing's request holds `structure` and a fold's holds `fold`; every other save of the
-- ledger is left out of the indexes.
CREATE INDEX IF NOT EXISTS ledger_structure_at ON ledger_submissions(at)
  WHERE iif(json_valid(request), coalesce(json_type(request, '$.structure'), json_type(request, '$.fold')), NULL) IS NOT NULL;
CREATE INDEX IF NOT EXISTS ledger_structure_actor ON ledger_submissions(actor, at)
  WHERE iif(json_valid(request), coalesce(json_type(request, '$.structure'), json_type(request, '$.fold')), NULL) IS NOT NULL;
CREATE INDEX IF NOT EXISTS ledger_structure_subject ON ledger_submissions(json_extract(response, '$.subject'), at)
  WHERE iif(json_valid(request), coalesce(json_type(request, '$.structure'), json_type(request, '$.fold')), NULL) IS NOT NULL;
