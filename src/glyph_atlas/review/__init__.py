"""The review service: a FastAPI app over one dataset directory and a SQLite store of events.

`server.create_app` builds the app for tests and for embedding; `server.serve` runs it with uvicorn
on the command line. `store.Store` holds the review events and the current state, `store.apply`
writes them back to the Parquet tables and `reviews.jsonl`, and `store.replay` rebuilds the state
from the tables and the log.
"""

from .server import cached_image, candidates_for, create_app, serve
from .store import (
    BadRequest,
    Conflict,
    LineRequest,
    NotFound,
    ReviewRequest,
    Store,
    StoreError,
    UnitRequest,
    apply,
    replay,
)

__all__ = [
    "BadRequest",
    "Conflict",
    "LineRequest",
    "NotFound",
    "ReviewRequest",
    "Store",
    "StoreError",
    "UnitRequest",
    "apply",
    "cached_image",
    "candidates_for",
    "create_app",
    "replay",
    "serve",
]
