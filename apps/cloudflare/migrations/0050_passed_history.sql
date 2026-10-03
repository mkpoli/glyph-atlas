-- The history lists each standing round once more for the crops it passed (`seen`), newest first:
-- over all rounds, by reviewer, and by the round's character, each read in index order.
CREATE INDEX IF NOT EXISTS submission_history ON submissions(at DESC, id DESC) WHERE undone=0;
CREATE INDEX IF NOT EXISTS submission_actor_history ON submissions(actor, at DESC, id DESC) WHERE undone=0;
CREATE INDEX IF NOT EXISTS submission_label_history ON submissions(json_extract(request,'$.input.label'), at DESC, id DESC) WHERE undone=0;
