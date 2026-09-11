# T40 Review service

Goal: a local service that serves pages, lines and units to the review interface and records
reviews as events that can be replayed.

Read first: `docs/schema.md` (reviews, identifiers), T01, T02, T04,
`docs/decisions/0002-schema-follow-ups.md`.

Outputs
- `src/kuzushiji_atlas/review/server.py` (FastAPI, MIT) over one dataset directory:
  `GET /documents` (paginated), `GET /pages/{id}`, `GET /images/{sha256}` (from the cache),
  `GET /pages/{id}/lines`, `GET /lines/{id}/units` (active units, with `revision` = the count of
  applied events on that unit), `GET /units/{id}/candidates` (T04 candidates with T31 scores when
  present and reference glyph images from the NINJAL table), `GET /queue?strategy=random|
  disagreement|unreviewed&document=…` (paginated), `POST /reviews` with a body of `ReviewRequest`
  records (`target_type`, `target_id`, `field`, `new`, `base_revision`, `client_id`,
  `idempotency_key`); the server assigns `id`, `at`, `actor` and writes `Review` events; a
  `base_revision` older than the unit's current revision answers 409 with the current state;
  a repeated `idempotency_key` answers the earlier result. Segmentation is one event: `field =
  "segmentation"`, `new = {"split": [{"box": …, "reading": …}, …]}` or `{"merge": [ids]}`; the
  server retires the inputs (`active=false`, `split_into` or `merged_into`) and creates the
  outputs with reviewer ids `{line_id}:m{n}` atomically. `POST /lines` creates a line a detector
  missed; `POST /units` creates a unit on an existing line.
- Storage: SQLite under `<dir>/review.sqlite` holding the events and the current state; `atlas
  review apply <dir>` writes the current state back to the Parquet tables and the events to
  `reviews.jsonl`; `atlas review replay <dir>` rebuilds the SQLite state from the tables and the
  log and checks that it matches.
- `atlas review serve <dir> --port 8770`.

Edge cases: a review of a retired unit (409); a split whose boxes leave the line box (accepted,
flagged); a crash between event write and state update (the event log is the source of truth;
replay repairs the state).

Tests: `TestClient` against a fixture dataset: each endpoint; a stale `base_revision`; an
idempotent repeat; a split then a merge; `replay` equals `apply`.

Acceptance: the calibration packages of T24 load; a box move, a split, a merge, a reading change
and a 字母 choice round-trip through `POST /reviews`, `apply` and `replay`.

Size: medium to large. Depends on: T01, T02, T04, T24.
