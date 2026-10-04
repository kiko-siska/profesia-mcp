from pathlib import Path

import pytest

from profesia_mcp import parsers

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_listing_parses_only_real_offer_rows():
    result = parsers.parse_listing(read("search.html"))
    assert len(result["jobs"]) == 20
    assert result["has_next"] is True and result["page"] == 1
    for job in result["jobs"]:
        assert job["id"].isdigit() and job["title"] and job["employer"] and job["location"]
        assert "search_id" not in job["url"]
    first = result["jobs"][0]
    assert first["salary"] and "EUR" in first["salary"]
    assert first["posted"]


def test_company_page_custom_list_layout():
    result = parsers.parse_listing(read("company_pro_hr.html"))
    assert len(result["jobs"]) > 5
    assert all(j["title"] and j["location"] for j in result["jobs"])


@pytest.mark.parametrize(
    "fixture,url,company",
    [
        ("detail_email_apply.html", "https://www.profesia.sk/praca/pro-hr/O5356737", "Pro HR"),
        ("detail_external_apply.html", "https://www.profesia.sk/praca/eset/O5153165", "ESET, spol. s r.o."),
        ("detail_standard.html", "https://www.profesia.sk/praca/carmeuse/O5366759", "Carmeuse Slovakia, s.r.o."),
        ("detail_plain_panel.html", "https://www.profesia.sk/praca/prima/O5364636", "Prima banka Slovensko, a.s."),
        ("detail_truncated_og.html", "https://www.profesia.sk/praca/lpp/O5368718", "LPP Slovakia, s.r.o."),
        ("detail_lidl.html", "https://www.profesia.sk/praca/lidl/O5356470", "Lidl Slovenská republika, s.r.o."),
        ("detail_banner_h1.html", "https://www.profesia.sk/praca/dt/O5270275", "Deutsche Telekom IT Solutions Slovakia"),
    ],
)
def test_job_detail_has_no_empty_core_fields_on_either_layout(fixture, url, company):
    job = parsers.parse_job(read(fixture), url)
    assert job["id"] == url.rsplit("/O", 1)[1]
    assert job["company"] == company
    assert job["company_url"] and "/C" in job["company_url"]
    for field in ("title", "location", "salary", "contract_type", "date_posted", "description"):
        assert job[field], f"{field} is empty"
    assert len(job["description"]) > 200


def test_standard_layout_has_sections():
    job = parsers.parse_job(read("detail_email_apply.html"), "https://www.profesia.sk/praca/pro-hr/O5356737")
    assert len(job["sections"]) >= 5


def test_application_info_email_method_is_deduplicated():
    info = parsers.parse_application_info(read("detail_email_apply.html"))
    assert info["emails"] == ["recruiter@example.com"]
    types = [m["type"] for m in info["methods"]]
    assert types.count("profesia_account") == 1 and "email" in types
    assert "send your CV" in info["selection_process"]


def test_application_info_external_site():
    info = parsers.parse_application_info(read("detail_external_apply.html"))
    assert [m["type"] for m in info["methods"]] == ["external_website"]


def test_indexes():
    locations = parsers.parse_index(read("locations.html"))
    slugs = {x["slug"] for x in locations}
    assert {"bratislava", "kosice", "bratislavsky-kraj"} <= slugs
    positions = parsers.parse_index(read("positions.html"), exclude=slugs)
    assert len(positions) > 100 and not slugs & {x["slug"] for x in positions}


@pytest.mark.parametrize("value,expected", [
    ("5356737", "5356737"), ("O5356737", "5356737"),
    ("https://www.profesia.sk/praca/pro-hr/O5356737?search_id=x", "5356737"), ("nonsense", None),
])
def test_extract_offer_id(value, expected):
    assert parsers.extract_offer_id(value) == expected


def test_banner_h1_layout_picks_offer_title_and_skips_info_box_in_description():
    job = parsers.parse_job(read("detail_banner_h1.html"), "https://www.profesia.sk/praca/dt/O5270275")
    assert job["title"].startswith("Senior SAP Developer")
    assert not job["description"].startswith("Place of work")


def test_standard_layout_fields():
    job = parsers.parse_job(read("detail_standard.html"), "https://www.profesia.sk/praca/carmeuse/O5366759")
    assert job["location"] == "Košice" and job["contract_type"] == "plný úväzok"
    assert job["salary"].startswith("Od 1 500")
