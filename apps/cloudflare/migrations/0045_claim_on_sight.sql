-- A browser's claim on its old reviewer id now holds at once, so no claim waits for an admin; any
-- still waiting is granted to the first account that asked, as a claim now would be.
INSERT OR IGNORE INTO actors(actor,user_id,via,at)
  SELECT actor,user_id,'legacy',at FROM actor_claims c
  WHERE at=(SELECT min(at) FROM actor_claims d WHERE d.actor=c.actor);
DROP TABLE IF EXISTS actor_claims;

-- Users named in the old ids' shape without holding that id are renamed `anon-…`, so a name of that
-- shape is always an old id its user holds.
UPDATE "user" SET name='anon-'||substr(lower(hex(randomblob(3))),1,6)
  -- D1 refuses a GLOB of eight character classes as too complex: the shape is tested as a length, a
  -- prefix and a tail with no character outside 0-9a-f.
  WHERE length(name)=17 AND substr(name,1,9)='reviewer-' AND substr(name,10) NOT GLOB '*[^0-9a-f]*'
    AND NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor="user".name AND a.user_id="user".id);
