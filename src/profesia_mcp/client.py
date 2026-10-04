"""Polite HTTP client for profesia.sk.

Enforces the site's robots.txt rules in code, rate-limits globally and caches
responses for a short time. Nothing here logs in or submits forms.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from urllib.parse import urlsplit

import httpx

BASE_URL = "https://www.profesia.sk"

# Override with PROFESIA_MCP_USER_AGENT if you fork the project.
USER_AGENT = os.environ.get(
    "PROFESIA_MCP_USER_AGENT",
    "profesia-mcp/0.1 (+https://github.com/kiko-siska/profesia-mcp; unofficial MCP server)",
)
MIN_INTERVAL = float(os.environ.get("PROFESIA_MCP_MIN_INTERVAL", "1.0"))  # seconds between requests
CACHE_TTL = float(os.environ.get("PROFESIA_MCP_CACHE_TTL", "600"))  # seconds

# Snapshot of the `User-agent: *` Disallow rules from https://www.profesia.sk/robots.txt
# (checked 2026-10-03). Patterns use robots.txt syntax: `*` wildcard, `$` end anchor.
DISALLOWED = [
    "/banner/", "/wap/", "/sk/", "/cz/", "/de/", "/en/*?", "/hu/*?", "/partner/",
    "/documents/apm/", "/documents/apm2/", "/documents/apm3/", "/documents/svp/",
    "/documents/user/", "/documents/users/",
    "/*company_details.php", "/*login_person.php", "/*order.php", "/*user_details.php",
    "/*list_similar_offers.php", "/*search_offers*form=", "/*offer_agent_details.php",
    "/*add_offer.php?offer_id=", "/*add_offer.php?copy_offer_id=", "/*search_cv.php",
    "/*list_cv.php", "/*send_cv.php", "/*add_cv_agent.php", "/*cv_agent_details.php",
    "/*send_page.php", "/*page_statistics.php", "/*recruit_rank_company.php",
    "/*recruit_rank_user.php", "/*print=1", "/*count_days=", "/*auth=", "/*ajax",
    "/*forum*parent_comment_id=", "/*interesting_add=", "/*export.php",
    "/*import_code_books.php", "/*referal_ip=", "/*show_map.php", "/*redirect.php",
]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile("^" + regex + ("$" if anchored else ""))


_DISALLOWED_RE = [_compile(p) for p in DISALLOWED]


class RobotsDisallowed(Exception):
    """Raised when a URL is excluded by profesia.sk's robots.txt."""


def is_allowed(url: str) -> bool:
    parts = urlsplit(url)
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    return not any(r.match(target) for r in _DISALLOWED_RE)


class ProfesiaClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.5"},
            follow_redirects=True,
            timeout=20.0,
        )
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._cache: dict[str, tuple[float, str, str]] = {}  # url -> (time, html, final_url)

    async def get_html(self, path_or_url: str) -> tuple[str, str]:
        """Fetch a page. Returns (html, final_url)."""
        url = path_or_url if path_or_url.startswith("http") else BASE_URL + path_or_url
        if urlsplit(url).netloc not in ("www.profesia.sk", "profesia.sk"):
            raise ValueError(f"Refusing to fetch non-profesia.sk URL: {url}")
        if not is_allowed(url):
            raise RobotsDisallowed(f"URL is disallowed by robots.txt: {url}")

        cached = self._cache.get(url)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1], cached[2]

        async with self._lock:  # serialises requests -> global rate limit
            wait = MIN_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await self._http.get(url)
            finally:
                self._last_request = time.monotonic()

        resp.raise_for_status()
        final_url = str(resp.url)
        if not is_allowed(final_url):
            raise RobotsDisallowed(f"Redirected to a disallowed URL: {final_url}")
        self._cache[url] = (time.monotonic(), resp.text, final_url)
        return resp.text, final_url

    async def aclose(self) -> None:
        await self._http.aclose()
