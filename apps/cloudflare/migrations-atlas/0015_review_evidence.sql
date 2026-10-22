-- What each review saved on this site showed the reviewer and what its crop was published as, frozen in
-- a content-addressed R2 object (`v3/review-evidence/<sha256>.json.gz`) before the submission commits;
-- a failed write leaves an object nothing names. Reviews moved from the old site are archived by page
-- (`v3/review-legacy/<page>.json.gz`), found by the event's `legacy` id.
ALTER TABLE submissions ADD COLUMN evidence TEXT CHECK(evidence IS NULL OR (length(evidence)=64 AND evidence NOT GLOB '*[^0-9a-f]*'));
