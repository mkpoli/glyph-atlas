-- The crops the Worker changed before it kept the list (`touched`, migration 0018): every crop a review
-- event of a generation above 0 names, and every corpus glyph that followed a forms decision (`bumps`),
-- listed at generation 0 so the next publication takes them.
--
-- It runs once, after the Worker that writes `touched` is deployed: a crop changed between migration
-- 0018 and that deploy is found here by its event or its `bumps`, and one changed after it is listed by
-- the Worker. Run before the deploy, it would miss the crops changed between the two. It reads `events`
-- and `crops` whole, once each, and writes one row for each crop it lists.
INSERT OR IGNORE INTO touched(crop, gen) SELECT DISTINCT crop, 0 FROM events WHERE gen > 0;
INSERT OR IGNORE INTO touched(crop, gen) SELECT id, 0 FROM crops WHERE bumps > 0;
