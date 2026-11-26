"""Croatian sources of internships and student jobs.

Only sources that publish their listings for machine reading are used:
  * Studentski centar Zagreb  - public WordPress REST API (/wp-json/wp/v2/jobs)
  * Studentski centar Dubrovnik - public WordPress REST API (/wp-json/wp/v2/student_poslovi)
  * Posao.hr - official RSS feed (filtered to internship / student postings)

Every fetcher returns normalized dicts:
    source, external_id, title, company, location, city, remote, kind, category, pay,
    work_period, tags, url, posted_at, expires_at, description
`kind` is "praksa" (internship) or "studentski" (student job via student servis / student contract).
"""
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "PraksaHR/0.2 (agregator studentskih praksi; poveznice vode na izvorni oglas)"
TIMEOUT = 25

# ---------- text helpers ----------

INTERNSHIP_RE = re.compile(
    r"\bpraks[aeiuo]m?\b|\bstručn\w* praks|\bpraktikant|\bintern(ship)?s?\b|\btrainee(ship)?\b|\bljetn\w* praks",
    re.I,
)
STUDENT_RE = re.compile(r"\bstudent\w*|\bza studente\b|\bstudentsk\w*", re.I)
REMOTE_RE = re.compile(r"\bod kuće\b|\bremote\b|\brad na daljinu\b|\bonline\b|\bhome office\b", re.I)


def classify(title: str, text: str = "") -> str | None:
    """'praksa', 'studentski' or None (posting isn't for students). Title wins over body text."""
    for t in (title or "", text or ""):
        if INTERNSHIP_RE.search(t):
            return "praksa"
    for t in (title or "", text or ""):
        if STUDENT_RE.search(t):
            return "studentski"
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
    "Virovitica", "Gospić", "Samobor", "Zaprešić", "Makarska", "Poreč", "Rovinj", "Opatija", "Krk",
]
_FOLDED_CITIES = [(_fold(c), c) for c in CITIES]


def detect_city(text: str, default: str | None = None) -> str | None:
    f = _fold(text)
    for folded, city in _FOLDED_CITIES:
        if re.search(rf"\b{re.escape(folded)}\b", f):
            return city
    return default


CATEGORY_RULES = [
    ("IT", r"\bit\b|\bit-|informati|programer|programiranj|developer|softver|software|\bdata\b|\bweb\b"),
    ("Marketing i promocija", r"marketing|promid|promoc|promotor|društven\w* mrež|social media"),
    ("Administracija i ured", r"administrat|ured|office|računovod|financ|ljudsk\w* potencijal|\bhr\b"),
    ("Trgovina i prodaja", r"trgovin|prodaj|prodava|blagajn"),
    ("Ugostiteljstvo i turizam", r"ugostitelj|turiz|konobar|kuhinj|recepc|hotel|šank"),
    ("Skladište, proizvodnja i fizički poslovi", r"skladi|proizvod|fizičk|čišćenj|vozač|dostav|montaž"),
    ("Instrukcije i rad s djecom", r"instrukc|poduk|djec|čuvanj"),
]
CATEGORIES = [c for c, _ in CATEGORY_RULES] + ["Ostalo"]


def categorize(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for name, pattern in CATEGORY_RULES:
        if re.search(pattern, blob):
            return name
    return "Ostalo"


def _get(url: str, **kw) -> httpx.Response:
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True, **kw)
    resp.raise_for_status()
    return resp


def _wp_paginate(url: str, max_pages: int = 15, **params) -> list[dict]:
    items, page = [], 1
    while page <= max_pages:
        resp = _get(url, params={"per_page": 100, "page": page, **params})
        batch = resp.json()
        items.extend(batch)
        if page >= int(resp.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    return items


def _wp_terms(url: str) -> dict[int, str]:
    try:
        return {t["id"]: unescape(t["name"]) for t in _wp_paginate(url, max_pages=3)}
    except Exception:
        return {}


def _hr_date(s: str) -> str | None:
    """'1.12.2026' / '01.12.2026.' -> ISO date."""
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", s or "")
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        return datetime(y, mo, d, 23, 59, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _pay(raw: str | None) -> str | None:
    """'7,00 EUR ' / '7,00 €/h' -> '7,00 €/h' (SC rates are hourly)."""
    raw = re.sub(r"\s+", " ", (raw or "").replace("EUR", "€")).strip()
    if not raw:
        return None
    return raw if "/h" in raw or "sat" in raw.lower() else f"{raw}/h"


# ---------- sources ----------

def fetch_sc_zagreb() -> list[dict]:
    base = "https://www.sczg.unizg.hr/wp-json/wp/v2"
    cats = _wp_terms(f"{base}/categories?parent=138")
    out = []
    for j in _wp_paginate(f"{base}/jobs"):
        m = j.get("meta") or {}
        title = unescape(m.get("title") or j["title"]["rendered"]).strip()
        description = html_to_text(" ".join(filter(None, [m.get("description"), m.get("whyme"), m.get("skills"),
                                                          m.get("other_description")])))
        address = unescape(m.get("city") or "").strip()
        sc_cat = " ".join(cats.get(c, "") for c in j.get("categories", []))
        work_start, work_end = (m.get("work_start") or "").strip(), (m.get("work_end") or "").strip()
        out.append({
            "source": "SC Zagreb",
            "external_id": str(j["id"]),
            "title": title,
            "company": unescape(m.get("company_name") or "").strip(),
            "location": address,
            "city": detect_city(address, default="Zagreb"),
            "remote": bool(REMOTE_RE.search(f"{title} {address} {description}")),
            "kind": classify(title, description) if INTERNSHIP_RE.search(f"{title} {description}") else "studentski",
            "category": _sc_category(sc_cat, title, description),
            "pay": _pay(m.get("payment_rate")),
            "work_period": " – ".join(x for x in (work_start, work_end) if x) or None,
            "tags": sc_cat.strip(),
            "url": j.get("link"),
            "posted_at": (j.get("date_gmt") or "") + "+00:00" if j.get("date_gmt") else None,
            "expires_at": (m.get("active_until") or "").replace("Z", "+00:00") or None,
            "description": description,
        })
    return out


def _sc_category(sc_cat: str, title: str, description: str) -> str:
    """Trust the student centre's own category; fall back to the text only when it's vague."""
    cat = categorize(sc_cat) if sc_cat.strip() else "Ostalo"
    return cat if cat != "Ostalo" else categorize(title, description)


SCDU_MAX_AGE_DAYS = 45


def fetch_sc_dubrovnik() -> list[dict]:
    base = "https://www.scdu.hr/wp-json/wp/v2"
    cats = _wp_terms(f"{base}/posao-kategorija")
    out = []
    # SC Dubrovnik keeps old postings published, so only take recent ones.
    since = (datetime.now(timezone.utc) - timedelta(days=SCDU_MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    for j in _wp_paginate(f"{base}/student_poslovi", max_pages=4, orderby="date", order="desc", after=since):
        title = unescape(j["title"]["rendered"]).strip()
        sc_cat = " ".join(cats.get(c, "") for c in j.get("posao-kategorija", []))
        out.append({
            "source": "SC Dubrovnik",
            "external_id": str(j["id"]),
            "title": title,
            "company": None,
            "location": "Dubrovnik",
            "city": "Dubrovnik",
            "remote": bool(REMOTE_RE.search(title)),
            "kind": "praksa" if INTERNSHIP_RE.search(title) else "studentski",
            "category": _sc_category(sc_cat, title, ""),
            "pay": None,
            "work_period": None,
            "tags": sc_cat.strip(),
            "url": j.get("link"),
            "posted_at": (j.get("date_gmt") or "") + "+00:00" if j.get("date_gmt") else None,
            "expires_at": None,
            "description": "",
        })
    return out


def fetch_posao_hr() -> list[dict]:
    """Official RSS of the latest postings on posao.hr; only internship/student ones are kept."""
    root = ET.fromstring(_get("https://www.posao.hr/rss/").content)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc_html = item.findtext("description") or ""
        desc = html_to_text(desc_html.replace("<div>", " | <div>"))
        kind = classify(title)  # body is only employer/place/deadline, so classify on title
        if not kind:
            continue
        company = re.search(r"Poslodavac:\s*([^|]+)", desc)
        place = re.search(r"Mjesto rada:\s*([^|]+)", desc)
        deadline = re.search(r"Rok za prijavu:\s*([^|]+)", desc)
        location = place.group(1).strip() if place else ""
        try:
            posted = parsedate_to_datetime(item.findtext("pubDate")).astimezone(timezone.utc).isoformat()
        except Exception:
            posted = None
        link = (item.findtext("link") or "").strip()
        out.append({
            "source": "Posao.hr",
            "external_id": item.findtext("guid") or link,
            "title": title,
            "company": company.group(1).strip() if company else None,
            "location": location,
            "city": detect_city(location),
            "remote": bool(REMOTE_RE.search(f"{title} {location}")),
            "kind": kind,
            "category": categorize(title),
            "pay": None,
            "work_period": None,
            "tags": "",
            "url": link,
            "posted_at": posted,
            "expires_at": _hr_date(deadline.group(1)) if deadline else None,
            "description": "",
        })
    return out


# full_sync=True: the source returns its complete active list, so postings missing from a successful
# fetch have been taken down and can be hidden. RSS only has the latest items, so it's not a full sync.
FETCHERS = {
    "SC Zagreb": (fetch_sc_zagreb, True),
    "SC Dubrovnik": (fetch_sc_dubrovnik, False),  # we only read the newest pages
    "Posao.hr": (fetch_posao_hr, False),
}
SOURCE_LINKS = {
    "SC Zagreb": "https://www.sczg.unizg.hr/poslovi",
    "SC Dubrovnik": "https://www.scdu.hr/",
    "Posao.hr": "https://www.posao.hr/",
}


def fetch_all() -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Run every fetcher; a failing source never breaks the others.
    Returns ({source: jobs} for sources that succeeded, {source: status})."""
    results, status = {}, {}
    for name, (fn, _full) in FETCHERS.items():
        try:
            got = fn()
            results[name] = got
            status[name] = f"ok ({len(got)})"
        except Exception as e:
            status[name] = f"error: {type(e).__name__}: {e}"[:200]
    return results, status
