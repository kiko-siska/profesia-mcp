"""HTML -> structured data for profesia.sk pages.

Profesia lets large employers use custom page designs, so detail pages come in
several layouts. The parser relies on stable structure (h1, `.upper-info-box-*`,
`.details-section`, <strong>Label:</strong> rows, `itemprop`) instead of label
text, because the page language varies per offer.
"""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

BASE_URL = "https://www.profesia.sk"

OFFER_ID_RE = re.compile(r"/O(\d+)")
COMPANY_ID_RE = re.compile(r"/praca/([a-z0-9-]+)/C(\d+)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_FACT_KEYS = {
    "location": ("place of work", "miesto práce", "miesto prace"),
    "salary": ("wage", "mzda", "mzdov", "plat"),
    "contract_type": ("contract type", "pracovný pomer", "druh pracovn", "typ pracovn", "forma spolupr"),
}


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def clean_text(node: Tag | None) -> str:
    """Text of a node with <br>/block breaks turned into newlines."""
    if node is None:
        return ""
    node = copy.copy(node)  # never mutate the caller's tree
    for br in node.find_all("br"):
        br.replace_with("\n")
    for li in node.find_all("li"):
        li.insert_before("\n• ")
    for block in node.find_all(["p", "div", "h3", "h4"]):
        block.insert_after("\n")
    text = node.get_text(" ")
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def offer_url(offer_id: str | int, href: str | None = None) -> str:
    """Canonical-ish URL without tracking params (profesia redirects any slug)."""
    if href:
        parts = urlsplit(urljoin(BASE_URL, href))
        return f"{BASE_URL}{parts.path}"
    return f"{BASE_URL}/praca/x/O{offer_id}"


def extract_offer_id(value: str) -> str | None:
    """Accept a numeric id, 'O123', or any profesia offer URL."""
    value = value.strip()
    if re.fullmatch(r"\d+", value):
        return value
    if re.fullmatch(r"O\d+", value):
        return value[1:]
    m = OFFER_ID_RE.search(value)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- listings


def parse_listing(html: str) -> dict[str, Any]:
    """Parse a search/company listing page into jobs + pagination info."""
    s = soup(html)
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in s.select("a[id^=offer]"):
        offer_id = anchor["id"].removeprefix("offer")
        if not offer_id.isdigit() or offer_id in seen:
            continue
        seen.add(offer_id)
        row = anchor.find_parent("li") or anchor.parent
        jobs.append(_parse_row(row, anchor, offer_id))

    page = 1
    active = s.select_one("ul.pagination li.active")
    if active and active.get_text(strip=True).isdigit():
        page = int(active.get_text(strip=True))
    return {
        "jobs": jobs,
        "page": page,
        "has_next": s.select_one("ul.pagination a.next") is not None,
    }


def _text(row: Tag, selector: str) -> str | None:
    node = row.select_one(selector)
    return clean_text(node) or None if node else None


def _parse_row(row: Tag, anchor: Tag, offer_id: str) -> dict[str, Any]:
    title_node = row.select_one("span.title") or anchor
    labels = [clean_text(x) for x in row.select(".label")]
    salary = None
    for label_node in row.select(".label"):
        if label_node.select_one("svg.money") or "EUR" in label_node.get_text():
            salary = clean_text(label_node)
            break
    company_link = row.select_one("a[href*='/C']")
    company_id = None
    if company_link:
        m = COMPANY_ID_RE.search(company_link.get("href", ""))
        company_id = m.group(2) if m else None
    posted = _text(row, ".list-footer .info") or _text(row, ".custom-item-add_date2")
    return {
        "id": offer_id,
        "title": clean_text(title_node),
        "employer": _text(row, "span.employer"),
        "location": _text(row, "span.job-location") or _text(row, ".custom-item-location"),
        "salary": salary,
        "labels": [lb for lb in labels if lb and lb != salary],
        "posted": posted,
        "company_id": company_id,
        "url": offer_url(offer_id, anchor.get("href")),
    }


def parse_index(html: str, exclude: set[str] | None = None) -> list[dict[str, str]]:
    """Parse /praca/zoznam-lokalit/ or /praca/zoznam-pozicii/ into slug+name."""
    exclude = exclude or set()
    out: dict[str, str] = {}
    for a in soup(html).find_all("a", href=True):
        m = re.fullmatch(r"/praca/([a-z0-9-]+)/", a["href"])
        if not m:
            continue
        slug = m.group(1)
        if slug.startswith("zoznam-") or slug in exclude:
            continue
        name = clean_text(a)
        if name and slug not in out:
            out[slug] = name
    return [{"slug": k, "name": v} for k, v in out.items()]


# --------------------------------------------------------------------------- detail


def parse_job(html: str, url: str) -> dict[str, Any]:
    s = soup(html)
    h1 = _offer_h1(s)
    title = clean_text(h1)

    offer_id = extract_offer_id(url)
    job: dict[str, Any] = {
        "id": offer_id,
        "url": offer_url(offer_id, urlsplit(url).path) if offer_id else url,
        "title": title,
        "company": None,
        "company_url": None,
        "location": None,
        "salary": None,
        "salary_note": None,
        "contract_type": None,
        "date_posted": None,
        "industry": None,
        "facts": {},
        "sections": {},
        "description": "",
    }

    job["company"] = _company_name(s, title)
    if not job["company"]:
        org = s.select_one("[itemprop=hiringOrganization]")
        job["company"] = clean_text(org) or None
    link = next((a for a in s.find_all("a", href=True) if COMPANY_ID_RE.fullmatch(urlsplit(a["href"]).path)), None)
    if link:
        job["company_url"] = urljoin(BASE_URL, urlsplit(link["href"]).path)

    for node in s.select("[itemprop]"):
        prop = node.get("itemprop")
        value = node.get("content") or clean_text(node)
        if prop == "datePosted":
            job["date_posted"] = value
        elif prop == "industry":
            job["industry"] = value

    _extract_facts(s, job)
    _extract_sections(s, job, h1)
    _apply_fact_keys(job)
    _fallbacks(s, job)

    salary_range = s.select_one(".salary-range")
    if salary_range:
        job["salary"] = clean_text(salary_range)
    salary_desc = s.select_one(".salary-desc")
    if salary_desc:
        job["salary_note"] = clean_text(salary_desc)

    if not job["description"]:
        og = s.find("meta", property="og:description")
        job["description"] = (og.get("content", "").strip() if og else "")
    return job


def _offer_h1(s: BeautifulSoup) -> Tag | None:
    """Custom designs may add a banner <h1> before the offer title; prefer the one matching og:title."""
    h1s = [h for h in s.find_all("h1") if clean_text(h)]
    if len(h1s) <= 1:
        return h1s[0] if h1s else None
    og = s.find("meta", property="og:title")
    og_title = re.sub(r"\s+", " ", og.get("content", "")) if og else ""
    for h in reversed(h1s):
        if re.sub(r"\s+", " ", clean_text(h)) in og_title:
            return h
    return h1s[-1]


def _company_name(s: BeautifulSoup, title: str) -> str | None:
    og = s.find("meta", property="og:title")
    og_title = og.get("content", "").strip() if og else ""
    if og_title and title:
        if og_title.endswith(f" - {title}"):
            return og_title[: -len(title) - 3].strip() or None
        if og_title.startswith(f"{title} - "):
            return og_title[len(title) + 3 :].strip() or None
    if s.title and s.title.string:  # "<title> - <company> | PROFESIA.SK"; title may be truncated with "..."
        t = re.sub(r"\s*\|\s*PROFESIA\.SK\s*$", "", s.title.string.strip(), flags=re.I)
        if " - " in t:
            return t.rsplit(" - ", 1)[1].strip() or None
    return None


def _extract_facts(s: BeautifulSoup, job: dict[str, Any]) -> None:
    facts: dict[str, str] = {}
    # Layout A: icon boxes (class suffix tells us the meaning, whatever the language)
    for box in s.select(".upper-info-box-item"):
        title = box.select_one(".upper-info-box-title")
        content = box.select_one(".upper-info-box-content")
        if not (title and content):
            continue
        facts[clean_text(title)] = clean_text(content)
        classes = " ".join(box.get("class", []))
        if "item-regions" in classes:
            job["location"] = clean_text(content)
        elif "item-jobtype" in classes:
            job["contract_type"] = clean_text(content)
        elif "item-salary" in classes:
            rng = content.select_one(".salary-range")
            job["salary"] = clean_text(rng) if rng else clean_text(content)
    # Layout D: company-prefixed boxes ("<x>-upper-info-box-item") with <strong>label</strong> + value
    for box in s.select("[class*=upper-info-box-item]"):
        strong = box.find("strong")
        if box.select_one(".upper-info-box-title") or not strong:
            continue
        label = clean_text(strong)
        body = copy.copy(box)
        body.find("strong").extract()
        rng = body.select_one(".salary-range")
        value = clean_text(rng) if rng else clean_text(body)
        if label and value:
            facts.setdefault(label, value)
    # Layout E: icon-less panel, <div><strong>label</strong><br><span>value</span></div>
    for box in s.select(".panel-body div"):
        strong = box.find("strong", recursive=False)
        if not strong or box.find("div"):
            continue
        label = clean_text(strong)
        body = copy.copy(box)
        body.find("strong").extract()
        value = clean_text(body)
        if label and value:
            facts.setdefault(label, value)
    # Layout C (standard template): icon rows; the icon class is language-neutral
    for svg in s.select("svg.icon"):
        icon = next((c for c in svg.get("class", []) if c.startswith("1-")), None)
        cell = svg.find_parent("div")
        body = cell.find_next_sibling("div") if cell else None
        strong = body.find("strong") if body else None
        if not (icon and body and strong):
            continue
        label = clean_text(strong)
        body = copy.copy(body)
        body.find("strong").extract()
        value = clean_text(body)
        facts[label] = value
        if icon == "1-location":
            job["location"] = value
        elif icon == "1-contract-type":
            job["contract_type"] = value
        elif icon == "1-salary":
            rng = body.select_one(".salary-range")
            job["salary"] = clean_text(rng) if rng else value
    # Layout B: <p><strong>Label:</strong> value</p>
    for p in s.select(".job-info p"):
        strong = p.find("strong")
        if not strong:
            continue
        label = clean_text(strong).rstrip(":").strip()
        strong.extract()
        value = clean_text(p)
        if label and value:
            facts.setdefault(label, value)
    job["facts"] = facts


def _apply_fact_keys(job: dict[str, Any]) -> None:
    for field, keys in _FACT_KEYS.items():
        if job[field]:
            continue
        for label, value in job["facts"].items():
            if any(k in label.lower() for k in keys):
                job[field] = value
                break


def _fallbacks(s: BeautifulSoup, job: dict[str, Any]) -> None:
    """Last-resort sources for fields some custom designs don't render in a known box."""
    if not job["contract_type"]:
        node = s.select_one("[itemprop=employmentType]")
        job["contract_type"] = clean_text(node) or None
    if not job["location"]:
        node = s.select_one("[itemprop=jobLocation]")
        job["location"] = clean_text(node) or None
    if not job["location"]:
        meta = s.find("meta", attrs={"name": "description"})
        m = re.search(r"(?:Miesto práce|Place of work):\s*(.+)$", meta.get("content", "") if meta else "")
        job["location"] = m.group(1).strip() if m else None


def _extract_sections(s: BeautifulSoup, job: dict[str, Any], h1: Tag | None) -> None:
    sections: dict[str, str] = {}
    for block in s.select(".details-section"):
        for heading in block.find_all("h4"):
            body = heading.find_next_sibling("div", class_="details-desc")
            if body:
                sections[clean_text(heading)] = clean_text(body)
    job["sections"] = sections
    if sections:
        job["description"] = "\n\n".join(f"{k}\n{v}" for k, v in sections.items())
        return
    # Custom layouts: take the blocks that follow the title/info box inside the same container
    if h1 is not None and h1.parent is not None:
        parts = []
        for sibling in h1.find_next_siblings():
            classes = sibling.get("class", [])
            if any("upper-info-box" in c for c in classes):
                continue
            text = clean_text(sibling)
            if text:
                parts.append(text)
        job["description"] = "\n\n".join(parts)
    if len(job["description"]) < 200:  # body lives outside the title's container in some designs
        area = s.select_one(".maintextearea")
        text = clean_text(area)
        if len(text) > len(job["description"]):
            job["description"] = text


# --------------------------------------------------------------------------- applying


def parse_application_info(html: str) -> dict[str, Any]:
    s = soup(html)
    methods: list[dict[str, str]] = []
    for a in s.find_all("a", href=True):
        href = a["href"]
        if "job-application-redirect" in href:
            m = re.search(r"job-application-redirect/(\d+)", href)
            methods.append({
                "type": "profesia_account",
                "url": f"{BASE_URL}/job-application-redirect/{m.group(1)}" if m else urljoin(BASE_URL, href),
                "note": "Applies through a Profesia.sk account; the user must log in and submit themselves.",
            })
        elif "external-reaction-redirect" in href:
            m = re.search(r"offerId=(\d+)", href)
            methods.append({
                "type": "external_website",
                "url": f"{BASE_URL}/external-reaction-redirect?offerId={m.group(1)}" if m else urljoin(BASE_URL, href),
                "note": "Redirects to the employer's own application page.",
            })
    emails: list[str] = []
    for a in s.select("a[href^='mailto:']"):
        emails.append(a["href"].removeprefix("mailto:").split("?")[0].strip())
    main = s.select_one(".maintextearea") or s.body
    emails += EMAIL_RE.findall(clean_text(main) if main else "")
    emails = [e for e in dict.fromkeys(x.lower() for x in emails) if not e.endswith("profesia.sk")]
    for e in emails:
        methods.append({"type": "email", "url": f"mailto:{e}", "note": "Send your CV by email."})

    methods = list({m["url"]: m for m in methods}.values())  # the page links the same URL several times

    process = ""
    for h in s.find_all(["h3", "h4"]):
        if re.search(r"selection process|výberov|výberu", clean_text(h), re.I):
            body = h.find_next_sibling("div", class_="details-desc")
            process = clean_text(body)
            break
    return {"methods": methods, "emails": emails, "selection_process": process}
