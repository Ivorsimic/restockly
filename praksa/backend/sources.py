"""Fetchers for public job APIs, filtered down to internships / student jobs.

Every fetcher returns a list of normalized dicts:
    source, external_id, title, company, location, remote, kind, tags, url, posted_at, description
`kind` is "praksa" (internship/traineeship) or "studentski" (working-student / part-time student job).
"""
import re
from datetime import datetime, timezone
from html import unescape

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "PraksaFinder/0.1 (student internship aggregator)"
TIMEOUT = 20

# Order matters: internship patterns are checked before student-job patterns.
INTERNSHIP_PATTERNS = [
    r"\bintern(ship)?s?\b", r"\binternships?\b", r"\bpraktikum", r"\bpraktikant", r"\bpraks[aeiu]\b",
    r"\bpraktikanti?\b", r"\btrainee(ship)?\b", r"\bstagiaire\b", r"\bstagist",
    r"\bpflichtpraktikum", r"\balternance\b", r"\bgraduate program", r"\bapprentice(ship)?\b",
]
STUDENT_PATTERNS = [
    r"\bwerkstudent", r"\bworking student\b", r"\bstudent job\b", r"\bstudentsk[aiou]", r"\bstudent assistant\b",
    r"\bstudentische", r"\bstudent (worker|position|developer|intern)\b",
]
_INTERN_RE = re.compile("|".join(INTERNSHIP_PATTERNS), re.I)
_STUDENT_RE = re.compile("|".join(STUDENT_PATTERNS), re.I)


def classify(title: str, extra: str = "") -> str | None:
    """Return 'praksa', 'studentski' or None if the posting isn't aimed at students.
    The title wins over job-type/tag metadata, which sources often fill in loosely."""
    for text in (title or "", extra or ""):
        if _INTERN_RE.search(text):
            return "praksa"
        if _STUDENT_RE.search(text):
            return "studentski"
    return None


def html_to_text(html: str, limit: int = 600) -> str:
    if not html:
        return ""
    text = BeautifulSoup(unescape(html), "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:limit] + ("…" if len(text) > limit else "")


def _iso_from_epoch(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _get(url: str, **kw):
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True, **kw)
    resp.raise_for_status()
    return resp.json()


def fetch_arbeitnow(pages: int = 5) -> list[dict]:
    """https://www.arbeitnow.com/api/job-board-api - free, Europe-focused (mostly DE), 300 jobs/page."""
    out = []
    for page in range(1, pages + 1):
        data = _get("https://www.arbeitnow.com/api/job-board-api", params={"page": page})
        for j in data.get("data", []):
            kind = classify(j.get("title", ""), " ".join(j.get("job_types") or []))
            if not kind:
                continue
            out.append({
                "source": "Arbeitnow",
                "external_id": j["slug"],
                "title": j.get("title", "").strip(),
                "company": j.get("company_name", ""),
                "location": j.get("location", ""),
                "remote": bool(j.get("remote")),
                "kind": kind,
                "tags": ", ".join(j.get("tags") or []),
                "url": j.get("url"),
                "posted_at": _iso_from_epoch(j.get("created_at")),
                "description": html_to_text(j.get("description", "")),
            })
        if not (data.get("links") or {}).get("next"):
            break
    return out


def fetch_remotive() -> list[dict]:
    """https://remotive.com/api/remote-jobs - remote jobs. Remotive asks clients not to poll more than
    a few times a day, so FETCHERS throttles this source (see MIN_INTERVAL_HOURS)."""
    out, seen = [], set()
    for term in ("intern", "trainee"):
        data = _get("https://remotive.com/api/remote-jobs", params={"search": term})
        for j in data.get("jobs", []):
            if j["id"] in seen:
                continue
            kind = classify(j.get("title", ""), j.get("job_type", "").replace("_", " "))
            if not kind:
                continue
            seen.add(j["id"])
            out.append({
                "source": "Remotive",
                "external_id": str(j["id"]),
                "title": j.get("title", "").strip(),
                "company": j.get("company_name", ""),
                "location": j.get("candidate_required_location", "") or "Remote",
                "remote": True,
                "kind": kind,
                "tags": ", ".join([j.get("category", "")] + (j.get("tags") or [])[:6]).strip(", "),
                "url": j.get("url"),
                "posted_at": (j.get("publication_date") or "") + "+00:00" if j.get("publication_date") else None,
                "description": html_to_text(j.get("description", "")),
            })
    return out


def fetch_remoteok() -> list[dict]:
    """https://remoteok.com/api - latest remote jobs. ToS: link back to Remote OK and credit it (done in UI)."""
    data = _get("https://remoteok.com/api")
    out = []
    for j in data[1:]:  # first element is the legal notice
        kind = classify(j.get("position", ""), " ".join(j.get("tags") or []))
        if not kind:
            continue
        out.append({
            "source": "Remote OK",
            "external_id": str(j.get("id")),
            "title": j.get("position", "").strip(),
            "company": j.get("company", ""),
            "location": j.get("location", "") or "Remote",
            "remote": True,
            "kind": kind,
            "tags": ", ".join(j.get("tags") or []),
            "url": j.get("url"),
            "posted_at": j.get("date"),
            "description": html_to_text(j.get("description", "")),
        })
    return out


# Minimum hours between calls per source (APIs ask to be polled politely).
MIN_INTERVAL_HOURS = {"Arbeitnow": 1, "Remotive": 6, "Remote OK": 1}
_last_run: dict[str, datetime] = {}

FETCHERS = {
    "Arbeitnow": fetch_arbeitnow,
    "Remotive": fetch_remotive,
    "Remote OK": fetch_remoteok,
}


def fetch_all() -> tuple[list[dict], dict[str, str]]:
    """Run every fetcher; one failing source never breaks the others. Returns (jobs, status_per_source)."""
    jobs, status = [], {}
    for name, fn in FETCHERS.items():
        last = _last_run.get(name)
        if last and (datetime.now(timezone.utc) - last).total_seconds() < MIN_INTERVAL_HOURS[name] * 3600 - 60:
            status[name] = "skipped (throttled)"
            continue
        _last_run[name] = datetime.now(timezone.utc)
        try:
            got = fn()
            jobs.extend(got)
            status[name] = f"ok ({len(got)})"
        except Exception as e:  # network / format errors
            status[name] = f"error: {type(e).__name__}: {e}"[:200]
    return jobs, status
