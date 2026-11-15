-- The crops the Worker changed before it kept the list (`touched`, migration 0018): every crop a review
-- event of a generation above 0 names, and every corpus glyph that followed a forms decision (`bumps`),
-- listed at generation 0 so the next publication takes them.
--
-- `answers.py --seed` applies it, once, under the stamp `migration:0019`; `publish.py --migrate` leaves
-- it alone. The order of a rollout:
--   1. `publish.py --migrate`: migration 0018 makes `touched` and `absorbing`;
--   2. deploy the Worker that lists touched crops;
--   3. `answers.py --seed` (`--seed --dry-run` prints the figures first);
--   4. publish.
-- A crop changed between steps 1 and 2 is found here by its event or its `bumps`, and one changed after
-- step 2 is listed by the Worker. Run before step 2, it would miss the crops changed between the two.
-- A publication of the answers is refused until the stamp is there. It reads `events` and `crops` whole,
-- once each, and writes one row for each crop it lists.
INSERT OR IGNORE INTO touched(crop, gen) SELECT DISTINCT crop, 0 FROM events WHERE gen > 0;
INSERT OR IGNORE INTO touched(crop, gen) SELECT id, 0 FROM crops WHERE bumps > 0;
