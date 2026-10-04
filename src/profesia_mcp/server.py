"""MCP server exposing read-only access to Profesia.sk job offers."""

from __future__ import annotations

import functools
import re
from typing import Any, Awaitable, Callable, Literal, TypeVar
from urllib.parse import urlencode

import httpx
from mcp.types import ToolAnnotations

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

try:  # mcp >= 2
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError

from . import parsers
from .client import ProfesiaClient, RobotsDisallowed

MAX_PAGES = 5  # hard cap per scrape_jobs call
MAX_DETAILS = 25  # hard cap on detail pages fetched per scrape_jobs call

mcp = _Server(
    "profesia",
    instructions=(
        "Tools for Profesia.sk, the largest Slovak job board (unofficial). "
        "Use list_locations / list_job_categories to find valid slugs, search_jobs to find offers, "
        "get_job for full details and get_application_info to help the user apply. "
        "This server cannot submit applications: the user must apply themselves via the returned link or email. "
        "Offer text is written in Slovak, English or other languages depending on the employer."
    ),
)
_client: ProfesiaClient | None = None
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def client() -> ProfesiaClient:
    global _client
    if _client is None:
        _client = ProfesiaClient()
    return _client


def _slug(value: str | None, what: str) -> str | None:
    if not value:
        return None
    value = value.strip().strip("/").lower()
    if not re.fullmatch(r"[a-z0-9-]+", value):
        raise ValueError(f"Invalid {what} '{value}': use a slug from list_locations / list_job_categories (e.g. 'bratislava').")
    return value


def build_search_path(
    query: str | None = None,
    location: str | None = None,
    category: str | None = None,
    salary_min: int | None = None,
    salary_period: Literal["m", "h"] = "m",
    remote: Literal["onsite", "remote", "hybrid"] | None = None,
    page: int = 1,
) -> str:
    """Build a /praca/... URL using only parameters that robots.txt allows."""
    loc, cat = _slug(location, "location"), _slug(category, "category")
    if loc and cat:
        raise ValueError("Profesia supports one path filter at a time: pass either location or category, not both. "
                         "Put the other one into `query`.")
    path = f"/praca/{loc or cat}/" if (loc or cat) else "/praca/"
    params: dict[str, Any] = {}
    if query:
        params["search_anywhere"] = query.strip()
    if salary_min:
        params["salary"] = int(salary_min)
        params["salary_period"] = salary_period
    if remote:
        params["remote_work"] = {"onsite": 0, "remote": 1, "hybrid": 2}[remote]
    if page and page > 1:
        params["page_num"] = int(page)
    return path + (f"?{urlencode(params)}" if params else "")


_T = TypeVar("_T")


def tool(fn: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
    """Register an MCP tool; expected failures become ToolError so the model sees a useful message.

    (mcp 2.x hides the text of ordinary exceptions raised inside tools.)
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> _T:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except RobotsDisallowed as exc:
            raise ToolError(f"Blocked by profesia.sk robots.txt, not requested: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise ToolError(f"profesia.sk answered HTTP {exc.response.status_code} for {exc.request.url}") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not reach profesia.sk: {exc!r}") from exc

    return mcp.tool(annotations=_READ_ONLY)(wrapper)


async def _fetch_offer(offer_id: str) -> tuple[str, str]:
    """Fetch an offer page; unknown/removed ids redirect to a listing or the homepage, so verify."""
    html, final_url = await client().get_html(f"/praca/x/O{offer_id}")
    if not re.search(rf"/O{offer_id}(?:$|[/?#])", final_url):
        raise ToolError(f"Offer {offer_id} was not found; it has probably expired or the id is wrong.")
    return html, final_url


@tool
async def search_jobs(
    query: str | None = None,
    location: str | None = None,
    category: str | None = None,
    salary_min: int | None = None,
    salary_period: Literal["m", "h"] = "m",
    remote: Literal["onsite", "remote", "hybrid"] | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """Search Profesia.sk job offers (20 per page).

    Args:
        query: Free-text keywords, e.g. "python developer".
        location: Location slug from list_locations, e.g. "bratislava", "kosice", "bratislavsky-kraj".
        category: Job category slug from list_job_categories, e.g. "java-programator-programatorka".
            Only one of location / category can be used; put the other in `query`.
        salary_min: Minimum salary (EUR) - per month when salary_period="m", per hour when "h".
        salary_period: "m" monthly or "h" hourly.
        remote: "onsite" (workplace only), "remote" (home only) or "hybrid" (partly from home).
        page: Page number, starting at 1.

    Returns jobs (id, title, employer, location, salary, labels, posted, url) and has_next.
    Pass a job id or url to get_job for the full description.
    """
    path = build_search_path(query, location, category, salary_min, salary_period, remote, page)
    html, _ = await client().get_html(path)
    result = parsers.parse_listing(html)
    result["search_url"] = parsers.BASE_URL + path
    return result


@tool
async def get_job(job: str) -> dict[str, Any]:
    """Get the full details of one job offer.

    Args:
        job: Offer id ("5356737", "O5356737") or a profesia.sk offer URL.

    Returns title, company, location, salary (+ note), contract type, date posted,
    the full description, and `sections` (requirements, languages, benefits, contact...)
    when the employer uses the standard layout.
    """
    offer_id = _offer_id(job)
    html, final_url = await _fetch_offer(offer_id)
    return parsers.parse_job(html, final_url)


@tool
async def get_application_info(job: str) -> dict[str, Any]:
    """Find out how to apply for a job, so the user (or you, drafting for them) can apply.

    This tool does NOT submit anything and never handles credentials. It returns the apply
    link(s) (Profesia account login or the employer's own site), any contact emails found
    in the offer, and the offer's "selection process" text. Typical use: read the offer with
    get_job, draft a tailored cover letter/email, and hand it to the user to send.

    Args:
        job: Offer id ("5356737", "O5356737") or a profesia.sk offer URL.
    """
    offer_id = _offer_id(job)
    html, final_url = await _fetch_offer(offer_id)
    info = parsers.parse_application_info(html)
    basics = parsers.parse_job(html, final_url)
    return {
        "id": offer_id,
        "title": basics["title"],
        "company": basics["company"],
        "offer_url": basics["url"],
        **info,
        "how_to_apply": (
            "Open one of the links in `methods` yourself. 'profesia_account' requires logging in to "
            "Profesia.sk; 'email' means sending your CV to that address (mention the position in the subject)."
            if info["methods"]
            else "No apply method found on the page; the offer may have expired. Check offer_url."
        ),
    }


@tool
async def company_jobs(company: str, page: int = 1) -> dict[str, Any]:
    """List open jobs of one employer.

    Args:
        company: Company page URL or path such as "https://www.profesia.sk/praca/pro-hr/C56404"
            or "pro-hr/C56404" (company slug + C-id; both appear in search_jobs / get_job results).
        page: Page number, starting at 1.

    Note: some large employers use custom-designed pages; if no jobs are returned for them,
    use search_jobs with the company name as `query` instead.
    """
    m = re.search(r"([a-z0-9-]+)/C(\d+)", company)
    if not m:
        raise ValueError("Expected a company URL like https://www.profesia.sk/praca/pro-hr/C56404 or 'pro-hr/C56404'.")
    path = f"/praca/{m.group(1)}/C{m.group(2)}" + (f"?page_num={int(page)}" if page > 1 else "")
    html, _ = await client().get_html(path)
    result = parsers.parse_listing(html)
    result["company_url"] = f"{parsers.BASE_URL}/praca/{m.group(1)}/C{m.group(2)}"
    return result


@tool
async def scrape_jobs(
    query: str | None = None,
    location: str | None = None,
    category: str | None = None,
    salary_min: int | None = None,
    salary_period: Literal["m", "h"] = "m",
    remote: Literal["onsite", "remote", "hybrid"] | None = None,
    max_pages: int = 2,
    include_details: bool = False,
    max_details: int = 10,
) -> dict[str, Any]:
    """Collect many search results at once (optionally with full details for each).

    Same filters as search_jobs. Requests are rate-limited (about 1/second), so large
    jobs are slow on purpose; hard caps: 5 pages and 25 detail pages per call.

    Args:
        max_pages: Result pages to fetch (1-5, 20 offers per page).
        include_details: Also fetch the full offer page for the first `max_details` jobs.
        max_details: How many offers to fetch details for (1-25).
    """
    max_pages = max(1, min(max_pages, MAX_PAGES))
    max_details = max(1, min(max_details, MAX_DETAILS))
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages_fetched = 0
    has_next = False
    for page in range(1, max_pages + 1):
        path = build_search_path(query, location, category, salary_min, salary_period, remote, page)
        html, _ = await client().get_html(path)
        parsed = parsers.parse_listing(html)
        pages_fetched += 1
        has_next = parsed["has_next"]
        for job in parsed["jobs"]:
            if job["id"] not in seen:
                seen.add(job["id"])
                jobs.append(job)
        if not has_next:
            break
    if include_details:
        for job in jobs[:max_details]:
            try:
                html, final_url = await _fetch_offer(job["id"])
                job["details"] = parsers.parse_job(html, final_url)
            except Exception as exc:  # keep going; report per-job failures
                job["details_error"] = str(exc)
    return {"count": len(jobs), "pages_fetched": pages_fetched, "has_more_pages": has_next, "jobs": jobs}


@tool
async def list_locations() -> list[dict[str, str]]:
    """List location slugs (regions and cities) usable as `location` in search_jobs."""
    html, _ = await client().get_html("/praca/zoznam-lokalit/")
    return parsers.parse_index(html)


@tool
async def list_job_categories() -> list[dict[str, str]]:
    """List job position/category slugs usable as `category` in search_jobs."""
    loc_html, _ = await client().get_html("/praca/zoznam-lokalit/")
    locations = {x["slug"] for x in parsers.parse_index(loc_html)}
    html, _ = await client().get_html("/praca/zoznam-pozicii/")
    return parsers.parse_index(html, exclude=locations)


def _offer_id(job: str) -> str:
    offer_id = parsers.extract_offer_id(job)
    if not offer_id:
        raise ValueError("Expected a numeric offer id (e.g. 5356737) or a profesia.sk offer URL containing '/O<id>'.")
    return offer_id


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
