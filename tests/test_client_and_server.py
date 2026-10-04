import pytest

from profesia_mcp.client import RobotsDisallowed, is_allowed, ProfesiaClient
from profesia_mcp.server import build_search_path


@pytest.mark.parametrize("url", [
    "https://www.profesia.sk/praca/?search_anywhere=python&sort_by=relevance",
    "https://www.profesia.sk/praca/bratislava/?remote_work=1&page_num=2",
    "https://www.profesia.sk/praca/pro-hr/O5356737",
    "https://www.profesia.sk/praca/zoznam-lokalit/",
])
def test_allowed(url):
    assert is_allowed(url)


@pytest.mark.parametrize("url", [
    "https://www.profesia.sk/praca/?count_days=7",
    "https://www.profesia.sk/search_offers.php?form=1",
    "https://www.profesia.sk/list_similar_offers.php?offer_id=1",
    "https://www.profesia.sk/praca/x/O1?print=1",
    "https://www.profesia.sk/send_cv.php",
    "https://www.profesia.sk/praca/ajax/foo",
    "https://www.profesia.sk/en/work?x=1",
])
def test_disallowed(url):
    assert not is_allowed(url)


async def test_client_refuses_disallowed_and_foreign_hosts():
    c = ProfesiaClient()
    try:
        with pytest.raises(RobotsDisallowed):
            await c.get_html("/praca/?count_days=7")
        with pytest.raises(ValueError):
            await c.get_html("https://example.com/")
    finally:
        await c.aclose()


def test_build_search_path():
    assert build_search_path() == "/praca/"
    assert build_search_path("python dev", location="bratislava", salary_min=2000, remote="remote", page=3) == \
        "/praca/bratislava/?search_anywhere=python+dev&salary=2000&salary_period=m&remote_work=1&page_num=3"
    assert build_search_path(category="java-programator-programatorka").startswith("/praca/java-programator")


def test_build_search_path_rejects_bad_input():
    with pytest.raises(ValueError):
        build_search_path(location="../etc")
    with pytest.raises(ValueError):
        build_search_path(location="bratislava", category="java-programator-programatorka")


class _StubClient:
    def __init__(self, final_url: str, html: str = "<html></html>"):
        self.final_url, self.html = final_url, html

    async def get_html(self, path):
        return self.html, self.final_url


async def test_unknown_offer_redirecting_to_homepage_is_an_error(monkeypatch):
    from profesia_mcp import server

    monkeypatch.setattr(server, "client", lambda: _StubClient("https://www.profesia.sk/"))
    with pytest.raises(server.ToolError, match="not found"):
        await server.get_job("999999999")
    with pytest.raises(server.ToolError, match="not found"):
        await server.get_application_info("999999999")


async def test_get_job_accepts_real_redirect_target(monkeypatch):
    from pathlib import Path

    from profesia_mcp import server

    html = (Path(__file__).parent / "fixtures" / "detail_standard.html").read_text(encoding="utf-8")
    monkeypatch.setattr(server, "client", lambda: _StubClient("https://www.profesia.sk/praca/carmeuse/O5366759", html))
    job = await server.get_job("O5366759")
    assert job["location"] == "Košice"


async def test_validation_errors_become_tool_errors_with_message():
    from profesia_mcp import server

    with pytest.raises(server.ToolError, match="one path filter"):
        await server.search_jobs(location="bratislava", category="x")
    with pytest.raises(server.ToolError, match="numeric offer id"):
        await server.get_job("nonsense")


async def test_cache_hit_keeps_final_redirect_url():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/praca/x/O1":
            return httpx.Response(301, headers={"location": "https://www.profesia.sk/praca/acme/O1"})
        return httpx.Response(200, text="<html>ok</html>")

    c = ProfesiaClient()
    await c._http.aclose()
    c._http = httpx.AsyncClient(base_url="https://www.profesia.sk", follow_redirects=True,
                                transport=httpx.MockTransport(handler))
    first = await c.get_html("/praca/x/O1")
    second = await c.get_html("/praca/x/O1")  # served from cache
    assert first == second and second[1].endswith("/praca/acme/O1")
    await c.aclose()
