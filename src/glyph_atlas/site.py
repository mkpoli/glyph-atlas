"""What the project's own tools send when they ask the site."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

SITE = "https://glyphatlas.org"
PASS_HEADER = "x-rate-limit-pass"


def pass_headers(url: str) -> dict[str, str]:
    """The header that keeps a request out of the site's rate limit, for a request to the site.

    The Worker counts each address's page and data requests and locks out one that keeps going
    over (`apps/cloudflare/src/rateLimit.ts`). A request carrying the Worker secret
    `RATE_LIMIT_PASS` is not counted. The secret is read from the environment and goes to the
    site alone, never to another host.
    """
    secret = os.environ.get("RATE_LIMIT_PASS")
    if secret and urlsplit(url).hostname == urlsplit(SITE).hostname:
        return {PASS_HEADER: secret}
    return {}
