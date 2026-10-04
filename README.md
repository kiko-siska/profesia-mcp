# profesia-mcp

An **unofficial** [Model Context Protocol](https://modelcontextprotocol.io) server for [Profesia.sk](https://www.profesia.sk), the largest Slovak job board. It lets an AI assistant (Claude Desktop, Claude Code, Cursor, …) search jobs, read full offers, bulk-collect results and prepare applications.

> Not affiliated with, endorsed by, or sponsored by Profesia.sk / Alma Career. Profesia is a trademark of its owner. Use it in line with Profesia's [terms of use](https://www.profesia.sk) and applicable law.

## Tools

| Tool | What it does |
|---|---|
| `search_jobs` | Search offers by keywords, location *or* category, minimum salary, remote/hybrid/on-site, with paging (20 per page). |
| `get_job` | Full offer by id or URL: title, company, location, salary (+ note), contract type, date posted, description and structured sections (requirements, languages, benefits, contact…). |
| `get_application_info` | How to apply: apply link(s), contact emails found in the offer, and the "selection process" text. |
| `company_jobs` | Open jobs of one employer (`pro-hr/C56404`). |
| `scrape_jobs` | Bulk collection across several pages, optionally with full details (hard caps: 5 pages, 25 detail pages per call). |
| `list_locations` / `list_job_categories` | Valid `location` / `category` slugs for `search_jobs`. |

### About applying (deliberate design choice)

This server **does not submit applications and never touches credentials.** Applying on Profesia goes through a logged-in account (or the employer's own site, or plain email), and automating that would mean handling your password and bypassing the site's protections. Instead, `get_application_info` returns the right link or email, so your assistant can read the offer, draft a tailored CV/cover letter or email, and *you* send it.

## Quick demo

Once installed, ask your assistant something like:

> "I'm a student who can work at most 20 hours a week. Find part-time Python / AI jobs in Bratislava that accept secondary-school students, and tell me how to apply."

It will call `search_jobs` (e.g. `category="na-dohodu-brigady"`, which is Profesia's part-time / agreement-based section), read the best matches with `get_job`, check the education requirements, and use `get_application_info` to give you the apply link or email.

## Install

Requires Python 3.10+. With [uv](https://docs.astral.sh/uv/):

```bash
uvx --from git+https://github.com/kiko-siska/profesia-mcp profesia-mcp
```

or from a clone: `pip install .` then run `profesia-mcp`.

### Claude Code

```bash
claude mcp add profesia -- uvx --from git+https://github.com/kiko-siska/profesia-mcp profesia-mcp
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "profesia": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/kiko-siska/profesia-mcp", "profesia-mcp"]
    }
  }
}
```

## Example prompts

- "Find remote Python jobs on Profesia paying at least 3000 EUR/month and summarise the top 5."
- "Compare the requirements of these three offers and tell me which fits my CV."
- "Read offer 5356737 and draft an email application for me in Slovak."

## Being a good citizen

The server is intentionally conservative:

- **robots.txt is enforced in code** (`client.py`): disallowed URLs (e.g. anything with `count_days=`, `form=`, `ajax`, `print=1`, `send_cv.php`, `list_similar_offers.php`) are never requested.
- **Rate limit:** one request at a time, at least 1 s apart (`PROFESIA_MCP_MIN_INTERVAL`).
- **Cache:** responses are cached for 10 minutes (`PROFESIA_MCP_CACHE_TTL`).
- **Honest User-Agent** that identifies this project. If you fork it, set `PROFESIA_MCP_USER_AGENT` (and update the default in `client.py`).
- Read-only: only `GET` requests to `www.profesia.sk`; no login, no form submission.

Please don't use `scrape_jobs` to mirror the site or republish its content. Offers belong to their employers and to Profesia.

## Limitations

- It parses HTML, which can change without notice; the offline tests in `tests/` (saved page fixtures for several different offer layouts, with personal contact details scrubbed) will tell you when a parser breaks.
- Employers may use custom page designs. Core fields are filled for all layouts tested, but `sections` is only available for the standard layout, and some large employers' company pages (`company_jobs`) are custom-built and may return no jobs; use `search_jobs` with the company name instead.
- Offer text is in Slovak, English or other languages, depending on the employer.
- With a keyword search Profesia only offers relevance sorting. The tracking `search_id` is stripped from URLs.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest            # offline; uses saved HTML fixtures
```

Works with `mcp` 1.x (FastMCP) and 2.x (MCPServer).

## License

MIT, see [LICENSE](LICENSE).
