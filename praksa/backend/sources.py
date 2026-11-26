"""Live sources of student internships (prakse) in Croatia.

Only sources that publish their listings openly for machine reading are used, always linking back
to the original posting:
  * Employer career sites on Workday (public job-search JSON the career site itself uses)
  * Rimac Group careers page
  * Studentski centar Zagreb - public WordPress REST API, internship postings only
  * Posao.hr - official RSS feed, internship postings only
  * Faculty / career-centre RSS feeds (internship news)
Big job boards whose terms forbid reuse (MojPosao, Studentski.hr, ...) are deliberately NOT scraped.

Every fetcher returns normalized dicts:
    source, external_id, title, company, location, city, remote, paid, category, pay, work_period,
    tags, url, posted_at, expires_at, description
"""
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "PraksaHR/0.3 (agregator studentskih praksi; poveznice vode na izvorni oglas)"
TIMEOUT = 25

# ---------- text helpers ----------

INTERNSHIP_RE = re.compile(
    r"\bpraks[aeiuo]m?\b|\bstručn\w* praks|\bpraktikant|\bintern(ship)?s?\b|\btrainee(ship)?s?\b|\bljetn\w* praks"
    r"|\bstažiranj|\bsummer camp\b",
    re.I,
)
# Faculty news mention lots of things; for them require a clearly internship-ish title.
STRICT_INTERNSHIP_RE = re.compile(
    r"\bpraks[aeiuo]m?\b|\bstručn\w* praks|\bintern(ship)?s?\b|\btrainee(ship)?s?\b|\bstažiranj|\bsummer camp\b", re.I)
NOT_INTERNSHIP_RE = re.compile(r"\bopć\w* praks|\bdobr\w* praks|\bpraksa i teorija|\binterna?\b(?! ?ship)"
                               r"|future vacanc|talent pool|open application|otvorena molba", re.I)
REMOTE_RE = re.compile(r"\bod kuće\b|\bremote\b|\brad na daljinu\b|\bhome office\b", re.I)
PAID_RE = re.compile(r"\bplaćen|\bpaid\b|\bnaknad|€|\beur\b", re.I)
UNPAID_RE = re.compile(r"\bneplaćen|\bunpaid\b|\bvolonter", re.I)


def is_internship(title: str, text: str = "", strict: bool = False) -> bool:
    if re.search(r"future vacanc|talent pool|open application|otvorena molba", title or "", re.I):
        return False
    if NOT_INTERNSHIP_RE.search(title or "") and not re.search(r"intern(ship)?s?\b", title or "", re.I):
        return False
    if (STRICT_INTERNSHIP_RE if strict else INTERNSHIP_RE).search(title or ""):
        return True
    return bool(text) and not strict and bool(re.search(r"\bstručn\w* praks|\bstudentsk\w* praks|\binternship\b", text, re.I))


def detect_paid(*texts: str) -> bool | None:
    blob = " ".join(t for t in texts if t)
    if UNPAID_RE.search(blob):
        return False
    if PAID_RE.search(blob):
        return True
    return None


def html_to_text(html: str, limit: int = 700) -> str:
    if not html:
        return ""
    text = BeautifulSoup(unescape(html), "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


CITIES = [
    "Zagreb", "Split", "Rijeka", "Osijek", "Zadar", "Dubrovnik", "Pula", "Varaždin", "Šibenik", "Karlovac",
    "Slavonski Brod", "Velika Gorica", "Sisak", "Vukovar", "Koprivnica", "Bjelovar", "Čakovec", "Požega",
    "Virovitica", "Gospić", "Samobor", "Zaprešić", "Vodnjan", "Poreč", "Rovinj", "Opatija", "Kutina", "Sveta Nedelja",
]
CITY_ALIASES = {"zg": "Zagreb", "kerestinec": "Zagreb", "st": "Split", "ri": "Rijeka", "os": "Osijek"}
_FOLDED_CITIES = [(_fold(c), c) for c in CITIES]


def detect_city(text: str, default: str | None = None) -> str | None:
    f = _fold(text)
    for folded, city in _FOLDED_CITIES:
        if re.search(rf"\b{re.escape(folded)}\b", f):
            return city
    for alias, city in CITY_ALIASES.items():
        if re.search(rf"\b{alias}\b", f):
            return city
    return default


CATEGORY_RULES = [
    ("IT i tehnologija", r"\bit\b|\bit-|informati|programer|programiranj|developer|softver|software|\bdata\b|devops|\bai\b"
                         r"|računarst|computer|cyber|solutions? engineer|5g|cloud|\bweb\b|tester|machine learning"),
    ("Inženjerstvo i proizvodnja", r"inženjer|engineer|strojar|elektrotehn|proizvod|manufactur|quality|kvalitet|građevin"
                                   r"|composite|mechanical|production|robot|automotive|energet|logisti"),
    ("Ekonomija i financije", r"ekonom|financ|računovod|accounting|audit|revizij|porez|\btax\b|\bbank|banking|analytic"
                              r"|business|poslovn|nabav|procurement|controlling|kontroling|investic|hnb|hanfa"),
    ("Pravo", r"\bpravo\b|\bprava\b|pravn|legal|\blaw\b|odvjet|sudsk"),
    ("Marketing, PR i mediji", r"marketing|\bpr\b|odnos\w* s javnošću|komunikacij|medij|novinar|social media|dizajn|design"
                               r"|graphic|content|brand"),
    ("Prodaja i korisnička podrška", r"prodaj|\bsales\b|customer|korisničk|support|podršk"),
    ("Ljudski potencijali", r"\bhr\b|ljudsk\w* potencijal|human resources|recruit|zapošljavanj|people"),
    ("Znanost, medicina i zdravstvo", r"medicin|farmac|pharma|laborator|kemij|biolog|zdravstv|znanstv|research|istraživ"),
]
CATEGORIES = [c for c, _ in CATEGORY_RULES] + ["Ostalo"]


def categorize(title: str, *more: str) -> str:
    """Category from the title; the longer texts are only consulted when the title says nothing."""
    for blob in (title or "", " ".join(t for t in (title, *more) if t)):
        for name, pattern in CATEGORY_RULES:
            if re.search(pattern, blob.lower()):
                return name
    return "Ostalo"


def _get(url: str, **kw) -> httpx.Response:
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True, **kw)
    resp.raise_for_status()
    return resp


def _job(**kw) -> dict:
    base = dict(company=None, location=None, city=None, remote=False, paid=None, category="Ostalo", pay=None,
                work_period=None, tags="", posted_at=None, expires_at=None, description="")
    base.update(kw)
    base["remote"] = bool(base["remote"] or REMOTE_RE.search(f"{base['title']} {base.get('location') or ''}"))
    return base


def _hr_date(s: str) -> str | None:
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", s or "")
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        return datetime(y, mo, d, 23, 59, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


# ---------- employer career sites ----------

# Workday career sites (company, host, tenant, site, paid). Find these on a company's careers page:
# links look like https://<host>/en-US/<site>/job/... and the tenant is the first part of the host.
WORKDAY_EMPLOYERS = [
    ("Infobip", "infobip.wd3.myworkdayjobs.com", "infobip", "InfobipCareers", True),
]


def _post_with_backoff(url: str, payload: dict, attempts: int = 4) -> httpx.Response:
    for attempt in range(attempts):
        resp = httpx.post(url, json=payload, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                          timeout=TIMEOUT)
        if resp.status_code != 429 or attempt == attempts - 1:
            resp.raise_for_status()
            return resp
        wait = resp.headers.get("Retry-After", "")
        time.sleep(min(int(wait), 120) if wait.isdigit() else 15 * 2 ** attempt)
    raise RuntimeError("unreachable")


def _workday_posted(text: str) -> str | None:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 13 Days Ago' / 'Posted 30+ Days Ago'."""
    now = datetime.now(timezone.utc)
    t = (text or "").lower()
    if "today" in t:
        return now.isoformat()
    if "yesterday" in t:
        return (now - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days", t)
    return (now - timedelta(days=int(m.group(1)))).isoformat() if m else None


def _workday_croatia_facet(facets: list) -> dict:
    """Find the 'Croatia' country facet so we only page through Croatian postings."""
    for f in facets or []:
        for group in [f] + list(f.get("values") or []):
            for v in group.get("values") or []:
                if re.search(r"croatia|hrvatska", v.get("descriptor") or "", re.I) and group.get("facetParameter"):
                    if "country" in group["facetParameter"].lower():
                        return {group["facetParameter"]: [v["id"]]}
    return {}


def fetch_workday() -> list[dict]:
    out = []
    for company, host, tenant, site, paid in WORKDAY_EMPLOYERS:
        api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        first = _post_with_backoff(api, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "intern"}).json()
        facet = _workday_croatia_facet(first.get("facets"))
        offset, total = 0, None
        while total is None or offset < min(total, 300):
            time.sleep(2)  # be gentle, Workday rate-limits
            data = _post_with_backoff(api, {"appliedFacets": facet, "limit": 20, "offset": offset,
                                            "searchText": "intern"}).json()
            total = data.get("total", 0) if total is None else total
            postings = data.get("jobPostings", [])
            if not postings:
                break
            for j in postings:
                path = j.get("externalPath", "")
                where = f"{j.get('locationsText', '')} {path.replace('-', ' ')}"
                if not re.search(r"croatia|hrvatska", where, re.I) or not is_internship(j.get("title", "")):
                    continue
                path_city = re.search(r"/job/([^/]+?)-Croatia/", path)
                city = detect_city(path_city.group(1).replace("-", " ") if path_city else where)
                locs = j.get("locationsText", "")
                out.append(_job(
                    source=company, external_id=path, title=j["title"].strip(), company=company,
                    location=locs if "Location" not in locs else f"{city or 'Hrvatska'} i druge lokacije",
                    city=city, paid=paid, category=categorize(j["title"]),
                    url=f"https://{host}/en-US/{site}{path}", posted_at=_workday_posted(j.get("postedOn")),
                ))
            offset += 20
    return out


def fetch_rimac() -> list[dict]:
    soup = BeautifulSoup(_get("https://careers.rimac-group.com/").text, "html.parser")
    out, seen = [], set()
    for li in soup.select("li[data-url]"):
        name = li.select_one(".job-name")
        loc = li.select_one(".c-autocomplete-list__item-location")
        title = name.get_text(" ", strip=True) if name else ""
        location = loc.get_text(" ", strip=True) if loc else ""
        if li["data-url"] in seen or not is_internship(title) or "HRV" not in location:
            continue
        seen.add(li["data-url"])
        out.append(_job(
            source="Rimac Group", external_id=li["data-url"], title=title, company="Rimac Group",
            location=location, city=detect_city(location, default="Zagreb"), paid=True,
            category=categorize(title), url=f"https://careers.rimac-group.com{li['data-url']}",
        ))
    return out


# ---------- Studentski centar Zagreb (internships only) ----------

def _pay(raw: str | None) -> str | None:
    raw = re.sub(r"\s+", " ", (raw or "").replace("EUR", "€")).strip()
    if not raw:
        return None
    return raw if "/h" in raw or "sat" in raw.lower() else f"{raw}/h"


def _wp_paginate(url: str, max_pages: int = 15, **params) -> list[dict]:
    items, page = [], 1
    while page <= max_pages:
        resp = _get(url, params={"per_page": 100, "page": page, **params})
        items.extend(resp.json())
        if page >= int(resp.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    return items


def fetch_sc_zagreb() -> list[dict]:
    out = []
    for j in _wp_paginate("https://www.sczg.unizg.hr/wp-json/wp/v2/jobs"):
        m = j.get("meta") or {}
        title = unescape(m.get("title") or j["title"]["rendered"]).strip()
        description = html_to_text(" ".join(filter(None, [m.get("description"), m.get("whyme"), m.get("skills"),
                                                          m.get("other_description")])))
        if not is_internship(title, description):
            continue
        address = unescape(m.get("city") or "").strip()
        work_start, work_end = (m.get("work_start") or "").strip(), (m.get("work_end") or "").strip()
        out.append(_job(
            source="SC Zagreb", external_id=str(j["id"]), title=title,
            company=unescape(m.get("company_name") or "").strip() or None,
            location=address, city=detect_city(address, default="Zagreb"),
            remote=bool(REMOTE_RE.search(f"{title} {description}")),
            paid=True,  # student-servis contract with an hourly rate
            category=categorize(title, description), pay=_pay(m.get("payment_rate")),
            work_period=" – ".join(x for x in (work_start, work_end) if x) or None,
            url=j.get("link"),
            posted_at=(j.get("date_gmt") + "+00:00") if j.get("date_gmt") else None,
            expires_at=(m.get("active_until") or "").replace("Z", "+00:00") or None,
            description=description,
        ))
    return out


# ---------- RSS feeds ----------

def _rss_items(url: str):
    root = ET.fromstring(_get(url).content.strip())
    for item in root.iter("item"):
        try:
            posted = parsedate_to_datetime(item.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            posted = None
        yield item, posted


def fetch_posao_hr() -> list[dict]:
    out = []
    for item, posted in _rss_items("https://www.posao.hr/rss/"):
        title = (item.findtext("title") or "").strip()
        if not is_internship(title):
            continue
        desc = html_to_text((item.findtext("description") or "").replace("<div>", " | <div>"))
        company = re.search(r"Poslodavac:\s*([^|]+)", desc)
        place = re.search(r"Mjesto rada:\s*([^|]+)", desc)
        deadline = re.search(r"Rok za prijavu:\s*([^|]+)", desc)
        location = place.group(1).strip() if place else ""
        link = (item.findtext("link") or "").strip()
        out.append(_job(
            source="Posao.hr", external_id=item.findtext("guid") or link, title=title,
            company=company.group(1).strip() if company else None, location=location, city=detect_city(location),
            paid=detect_paid(title), category=categorize(title), url=link,
            posted_at=posted.isoformat() if posted else None,
            expires_at=_hr_date(deadline.group(1)) if deadline else None,
        ))
    return out


# (source name, feed URL) - faculty/career-centre feeds that carry internship calls.
FACULTY_FEEDS = [
    ("FESB Split", "https://www.fesb.unist.hr/category/studenti-prakse/feed/"),
    ("EFOS Osijek", "https://www.efos.unios.hr/category/karijere/feed/"),
    ("Sveučilište u Rijeci (SSC)", "https://ssc.uniri.hr/tag/strucna-praksa/feed/"),
]
FACULTY_MAX_AGE_DAYS = 120


def fetch_faculty_feeds() -> list[dict]:
    out, cutoff = [], datetime.now(timezone.utc) - timedelta(days=FACULTY_MAX_AGE_DAYS)
    for source, url in FACULTY_FEEDS:
        for item, posted in _rss_items(url):
            title = unescape((item.findtext("title") or "").strip())
            if not posted or posted < cutoff or not is_internship(title, strict=True):
                continue
            desc = html_to_text(item.findtext("description") or "", limit=400)
            desc = re.sub(r"The post .* appeared first on .*$", "", desc).strip()
            link = (item.findtext("link") or "").strip()
            out.append(_job(
                source=source, external_id=item.findtext("guid") or link, title=title,
                city=detect_city(f"{title} {desc}"), paid=detect_paid(title, desc),
                category=categorize(title, desc), url=link, posted_at=posted.isoformat(),
                # News posts carry no deadline; hide them after a while so stale calls disappear.
                expires_at=(posted + timedelta(days=FACULTY_MAX_AGE_DAYS)).isoformat(),
                description=desc,
            ))
    return out


# name -> (fetcher, full_sync, homepage). full_sync=True: the fetcher returns every currently open
# internship of that source, so postings missing from a successful fetch were taken down.
FETCHERS = {
    "Infobip": (fetch_workday, True, "https://www.infobip.com/internship"),
    "Rimac Group": (fetch_rimac, True, "https://careers.rimac-group.com/"),
    "SC Zagreb": (fetch_sc_zagreb, True, "https://www.sczg.unizg.hr/poslovi"),
    "Posao.hr": (fetch_posao_hr, False, "https://www.posao.hr/"),
    "Fakulteti": (fetch_faculty_feeds, False, None),
}


def fetch_all() -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Run every fetcher; a failing source never breaks the others.
    Returns ({fetcher name: jobs} for fetchers that succeeded, {fetcher name: status})."""
    results, status = {}, {}
    for name, (fn, _full, _home) in FETCHERS.items():
        try:
            got = fn()
            results[name] = got
            status[name] = f"ok ({len(got)})"
        except Exception as e:
            status[name] = f"error: {type(e).__name__}: {e}"[:200]
    return results, status
