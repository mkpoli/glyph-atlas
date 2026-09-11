# T40 Review service

Goal: a local service that serves pages, lines and units to the review interface and appends
reviews.

Read first: `docs/schema.md` (reviews), T01, T02, `docs/decisions/0002-schema-follow-ups.md`.

Outputs
- `src/kuzushiji_atlas/review/server.py` (FastAPI, MIT): `GET /documents`, `GET /pages/{id}`
  (page record, image URL served from the cache at `/images/{sha256}`), `GET /pages/{id}/lines`,
  `GET /lines/{id}/units`, `GET /units/{id}/candidates` (T04 candidates with T31 scores when
  present), `POST /reviews` (a list of `Review` records; validated; appended to `reviews.jsonl`
  with a server-assigned id and time), `POST /apply` (fold the log into the tables: current values
  replaced by the latest review, segmentation events create and retire units), `GET /queue?strategy=disagreement|random`.
- `atlas review serve <dir> --port 8770`.
- One writer: the server holds the only handle on `reviews.jsonl`.

Edge cases: a review for a unit that a later split retired (reject with 409); concurrent posts
(serialised).

Tests: the endpoints against a fixture dataset with `TestClient`; apply of a split event.

Acceptance: the pilot packages of T24 load; a split, a merge, a box move and a 字母 choice round-trip
through `POST /reviews` and `POST /apply`.

Size: medium. Depends on: T01, T02, T04.
