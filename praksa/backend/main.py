import os
import threading
from contextlib import asynccontextmanager
from datetime import date
from html import escape
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, HttpUrl

import db
import programs
from notifier import send_alert, DEV_MODE as EMAIL_DEV_MODE
from sources import CATEGORIES, CITIES, FACULTY_FEEDS, FETCHERS, fetch_all

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8002").rstrip("/")
FETCH_INTERVAL_MINUTES = int(os.environ.get("FETCH_INTERVAL_MINUTES", "60"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")  # receives review links for company posts
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
CITY_CHOICES = CITIES + ["Inozemstvo"]

scheduler = BackgroundScheduler()
_fetch_lock = threading.Lock()


def plural(n: int, one: str, few: str, many: str) -> str:
    """Croatian plural: 1 praksa, 2-4 prakse, 5+ praksi (11-14 -> many)."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


# ---------- matching + alerts ----------

def matches(alert: dict, job: dict) -> bool:
    haystack = " ".join(str(job.get(k) or "") for k in ("title", "company", "tags", "description", "location")).lower()
    if any(word not in haystack for word in (alert.get("keywords") or "").lower().split()):
        return False
    for field in ("city", "category"):
        if alert.get(field) and alert[field] != job.get(field):
            return False
    if alert.get("remote_only") and not job.get("remote"):
        return False
    if alert.get("paid_only") and not job.get("paid"):
        return False
    return True


def program_matches(alert: dict, p: dict) -> bool:
    haystack = " ".join([p["organization"], p["name"], p["description"], " ".join(p["categories"])]).lower()
    if any(word not in haystack for word in (alert.get("keywords") or "").lower().split()):
        return False
    if alert.get("category") and alert["category"] not in p["categories"]:
        return False
    if alert.get("city") and p["cities"] and alert["city"] not in p["cities"]:
        return False
    if alert.get("paid_only") and not p.get("paid"):
        return False
    return not alert.get("remote_only")


def _unsubscribe_line(alert: dict) -> str:
    return f"\nOdjava od ove obavijesti: {BASE_URL}/api/alerts/unsubscribe/{alert['token']}"


def notify_alerts(new_jobs: list[dict]) -> int:
    sent = 0
    for alert in db.active_alerts():
        hits = [j for j in new_jobs if matches(alert, j)]
        if not hits:
            continue
        n = len(hits)
        lines = [f"{plural(n, 'Pojavila se', 'Pojavile su se', 'Pojavilo se')} {n} "
                 f"{plural(n, 'nova praksa', 'nove prakse', 'novih praksi')} za tvoju pretragu:\n"]
        for j in hits[:25]:
            bits = [b for b in (j.get("company"), j.get("city") or j.get("location"), j.get("pay")) if b]
            lines.append(f"• {j['title']}" + (f" — {', '.join(bits)}" if bits else ""))
            lines.append(f"  {j.get('url') or ('Prijave: ' + (j.get('contact_email') or ''))}")
        if n > 25:
            lines.append(f"\n…i još {n - 25}. Sve prakse vidi na {BASE_URL}")
        lines.append(_unsubscribe_line(alert))
        try:
            send_alert(alert["email"], f"{n} {plural(n, 'nova praksa', 'nove prakse', 'novih praksi')}", "\n".join(lines))
            sent += 1
        except Exception as e:
            print(f"[alert] failed for alert {alert['id']}: {e}")
    return sent


def notify_program_windows(today: date | None = None) -> int:
    """Once a year per programme, tell matching subscribers that applications usually open now."""
    today = today or date.today()
    opening = [p for p in programs.load()
               if isinstance(p.get("window_months"), list) and today.month in p["window_months"]
               and not db.program_notified(p["id"], today.year)]
    if not opening:
        return 0
    sent = 0
    for alert in db.active_alerts():
        if not alert.get("include_programs"):
            continue
        hits = [p for p in opening if program_matches(alert, p)]
        if not hits:
            continue
        lines = ["Otvara se sezona prijava za programe praksi koji te zanimaju:\n"]
        for p in hits:
            lines.append(f"• {p['organization']}: {p['name']}\n  {p['window_note']}\n  {p['url']}")
        lines.append("\nRokovi se razlikuju iz godine u godinu, pa provjeri točan datum na službenoj stranici.")
        lines.append(_unsubscribe_line(alert))
        try:
            send_alert(alert["email"], "Otvaraju se prijave za prakse", "\n".join(lines))
            sent += 1
        except Exception as e:
            print(f"[program-alert] failed for alert {alert['id']}: {e}")
    for p in opening:
        db.mark_program_notified(p["id"], today.year)
    return sent


def run_fetch(send_notifications: bool = True) -> dict:
    if not _fetch_lock.acquire(blocking=False):
        return {"skipped": "fetch already running"}
    try:
        results, status = fetch_all()
        new = []
        for name, jobs in results.items():
            full_sync = FETCHERS[name][1]
            by_source: dict[str, list] = {}
            for j in jobs:
                by_source.setdefault(j["source"], []).append(j)
            for source, items in by_source.items():
                new += db.sync_source(source, items, full_sync=full_sync)
        sent = notify_alerts(new) if send_notifications else 0
        sent += notify_program_windows() if send_notifications else 0
        status_str = "; ".join(f"{k}: {v}" for k, v in status.items())
        db.log_fetch(len(new), status_str)
        print(f"[fetch] {len(new)} new, {sent} alert emails | {status_str}")
        return {"new": len(new), "alerts_sent": sent, "sources": status}
    finally:
        _fetch_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    first_run = db.stats()["last_fetch"] is None
    # Initial fetch in the background so the server starts instantly. No emails on the very first
    # fill, otherwise every alert would match the whole backlog.
    threading.Thread(target=run_fetch, kwargs={"send_notifications": not first_run}, daemon=True).start()
    scheduler.add_job(run_fetch, "interval", minutes=FETCH_INTERVAL_MINUTES, id="fetch")
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Praksa — studentske prakse u Hrvatskoj", lifespan=lifespan)


def page(title: str, message: str) -> str:
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}</title><body style='font-family:system-ui,sans-serif;padding:2rem;max-width:40rem;margin:auto'>"
            f"<h2>{escape(title)}</h2><p>{escape(message)}</p><p><a href='/'>← Natrag na prakse</a></p>")


# ---------- public API ----------

@app.get("/api/internships")
def list_internships(
    q: str | None = None,
    city: str | None = None,
    category: str | None = None,
    paid: bool = False,
    remote: bool = False,
    source: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    total, items = db.search(q, city, category, paid, remote, source, limit=per_page, offset=(page - 1) * per_page)
    return {"total": total, "page": page, "per_page": per_page, "items": items}


@app.get("/api/programs")
def list_programs():
    by_company = db.stats()["by_company"]
    items = programs.with_status()
    for p in items:
        p["open_now"] = by_company.get(p.get("live_source") or p["organization"], 0)
    return items


@app.get("/api/meta")
def get_meta():
    sources = [{"name": n, "url": home} for n, (_fn, _full, home) in FETCHERS.items() if n != "Fakulteti"]
    sources += [{"name": n, "url": url.split("/category")[0].split("/tag")[0]} for n, url in FACULTY_FEEDS]
    sources.append({"name": db.MANUAL_SOURCE, "url": None})
    return {**db.stats(), "cities": CITY_CHOICES, "categories": CATEGORIES, "sources": sources,
            "program_count": len(programs.load()), "email_dev_mode": EMAIL_DEV_MODE}


class NewInternship(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    company: str = Field(min_length=2, max_length=200)
    city: str | None = Field(None, max_length=100)
    location: str | None = Field(None, max_length=200)
    remote: bool = False
    paid: bool | None = None
    category: str | None = None
    pay: str | None = Field(None, max_length=100)
    work_period: str | None = Field(None, max_length=100)
    url: HttpUrl | None = None
    description: str | None = Field(None, max_length=3000)
    contact_email: EmailStr


@app.post("/api/internships", status_code=201)
def post_internship(body: NewInternship):
    if body.city and body.city not in CITY_CHOICES:
        raise HTTPException(400, "Odaberi grad s popisa.")
    if body.category and body.category not in CATEGORIES:
        raise HTTPException(400, "Odaberi područje s popisa.")
    data = body.model_dump()
    data["url"] = str(body.url) if body.url else None
    data["location"] = data.get("location") or data.get("city")
    data["category"] = data.get("category") or "Ostalo"
    new_id, token = db.add_manual_internship(data)

    review = (f"Nova praksa čeka odobrenje:\n\n{body.title}\n{body.company} · {data['location'] or '-'}\n"
              f"Kontakt: {body.contact_email}\nLink: {data['url'] or '-'}\n\n{body.description or ''}\n\n"
              f"Pregledaj i odobri/odbij: {BASE_URL}/api/admin/moderate/{token}")
    if ADMIN_EMAIL:
        send_alert(ADMIN_EMAIL, f"[Praksa] Za odobrenje: {body.title}", review)
    else:
        print(f"[moderation] ADMIN_EMAIL not set. {review}")
    send_alert(body.contact_email, "Zaprimili smo vaš oglas",
               f"Hvala! Oglas \"{body.title}\" je zaprimljen i bit će objavljen nakon kratke provjere.")
    return {"id": new_id, "status": "pending"}


class NewAlert(BaseModel):
    email: EmailStr
    keywords: str | None = Field(None, max_length=200)
    city: str | None = None
    category: str | None = None
    paid_only: bool = False
    remote_only: bool = False
    include_programs: bool = True


@app.post("/api/alerts", status_code=201)
def create_alert(body: NewAlert):
    if body.city and body.city not in CITY_CHOICES:
        raise HTTPException(400, "Odaberi grad s popisa.")
    if body.category and body.category not in CATEGORIES:
        raise HTTPException(400, "Odaberi područje s popisa.")
    token = db.create_alert(body.email, (body.keywords or "").strip() or None, body.city, body.category,
                            body.paid_only, body.remote_only, body.include_programs)
    summary = ", ".join(x for x in (body.keywords, body.city, body.category,
                                    "samo plaćene" if body.paid_only else None,
                                    "rad od kuće" if body.remote_only else None) if x) or "sve prakse"
    send_alert(
        body.email,
        "Potvrdi obavijest o praksama",
        f"Netko (nadamo se ti) je zatražio obavijesti o novim praksama za: {summary}.\n\n"
        f"Potvrdi klikom na link:\n{BASE_URL}/api/alerts/confirm/{token}\n\n"
        f"Ako to nisi bio/la ti, samo zanemari ovaj mail.",
    )
    return {"ok": True, "email_dev_mode": EMAIL_DEV_MODE}


@app.get("/api/alerts/confirm/{token}", response_class=HTMLResponse)
def confirm(token: str):
    if db.confirm_alert(token):
        return page("Obavijest potvrđena ✓", "Javit ćemo ti mailom kad se pojavi nova praksa i kad se otvore prijave za programe praksi.")
    return page("Link nije valjan", "Obavijest ne postoji ili je isključena.")


@app.get("/api/alerts/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(token: str):
    if db.deactivate_alert(token):
        return page("Odjava uspješna", "Više nećeš primati ovu obavijest.")
    return page("Već odjavljeno", "Obavijest ne postoji ili je već isključena.")


# ---------- admin ----------

@app.get("/api/admin/moderate/{token}", response_class=HTMLResponse)
def moderate_page(token: str):
    """Shows the post with approve/reject buttons. The decision is a POST so that mail clients and
    link scanners that prefetch URLs can't approve anything by accident."""
    post = next((p for p in db.pending_posts() if p["moderation_token"] == token), None)
    if not post:
        return page("Ništa za napraviti", "Oglas ne postoji ili je već obrađen.")
    paid = {1: "da", 0: "ne"}.get(post.get("paid"), "-")
    rows = "".join(f"<tr><th style='text-align:left;padding-right:1em;vertical-align:top'>{escape(k)}</th><td>{escape(str(v or '-'))}</td></tr>"
                   for k, v in (("Naziv", post["title"]), ("Tvrtka", post["company"]), ("Lokacija", post["location"]),
                                ("Plaćeno", paid), ("Područje", post["category"]), ("Naknada", post["pay"]),
                                ("Razdoblje", post["work_period"]), ("Link", post["url"]),
                                ("Kontakt", post["contact_email"]), ("Opis", post["description"])))
    btn = "font:inherit;padding:.6em 1.2em;border:0;border-radius:8px;color:#fff;cursor:pointer"
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Odobravanje oglasa</title><body style='font-family:system-ui,sans-serif;padding:2rem;max-width:44rem;margin:auto'>"
            f"<h2>Oglas čeka odobrenje</h2><table>{rows}</table><p>"
            f"<form method=post style='display:inline'><input type=hidden name=action value=approve>"
            f"<button style='{btn};background:#1d7a4a'>Odobri i objavi</button></form> "
            f"<form method=post style='display:inline'><input type=hidden name=action value=reject>"
            f"<button style='{btn};background:#b3261e'>Odbij</button></form></p>")


@app.post("/api/admin/moderate/{token}", response_class=HTMLResponse)
def moderate(token: str, action: str = Form(pattern="^(approve|reject)$")):
    post = db.moderate(token, approve=action == "approve")
    if not post:
        return page("Ništa za napraviti", "Oglas ne postoji ili je već obrađen.")
    if action == "approve":
        post["approved"] = 1
        sent = notify_alerts([post])
        send_alert(post["contact_email"], "Vaš oglas je objavljen",
                   f"Oglas \"{post['title']}\" je sada vidljiv na {BASE_URL}")
        return page("Oglas odobren ✓", f"\"{post['title']}\" je objavljen. Poslano obavijesti: {sent}.")
    return page("Oglas odbijen", f"\"{post['title']}\" neće biti objavljen.")


def _require_admin(token: str | None):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "Forbidden")


@app.get("/api/admin/pending")
def list_pending(x_admin_token: str | None = Header(None)):
    _require_admin(x_admin_token)
    return db.pending_posts()


@app.post("/api/admin/fetch")
def trigger_fetch(x_admin_token: str | None = Header(None)):
    _require_admin(x_admin_token)
    return run_fetch()


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
