import os
import threading
from contextlib import asynccontextmanager
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
from notifier import send_alert, DEV_MODE as EMAIL_DEV_MODE
from sources import CATEGORIES, CITIES, FETCHERS, SOURCE_LINKS, fetch_all

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8002").rstrip("/")
FETCH_INTERVAL_MINUTES = int(os.environ.get("FETCH_INTERVAL_MINUTES", "60"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")  # receives approve/reject links for company posts
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

scheduler = BackgroundScheduler()
_fetch_lock = threading.Lock()

KIND_PATTERN = "^(praksa|studentski)$"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Croatian plural: 1 oglas, 2-4 oglasa, 5+ oglasa (11-14 -> many)."""
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
    for field in ("city", "category", "kind"):
        if alert.get(field) and alert[field] != job.get(field):
            return False
    if alert.get("remote_only") and not job.get("remote"):
        return False
    return True


def notify_alerts(new_jobs: list[dict]) -> int:
    sent = 0
    for alert in db.active_alerts():
        hits = [j for j in new_jobs if matches(alert, j)]
        if not hits:
            continue
        hits.sort(key=lambda j: j.get("kind") != "praksa")  # internships first
        lines = [f"Pronašli smo {len(hits)} {plural(len(hits), 'novi oglas', 'nova oglasa', 'novih oglasa')} za tvoju pretragu:\n"]
        for j in hits[:25]:
            bits = [b for b in (j.get("company"), j.get("city") or j.get("location"), j.get("pay")) if b]
            lines.append(f"• {j['title']}" + (f" — {', '.join(bits)}" if bits else ""))
            lines.append(f"  {j.get('url') or ('Prijave: ' + (j.get('contact_email') or ''))}")
        if len(hits) > 25:
            lines.append(f"\n…i još {len(hits) - 25}. Sve oglase vidi na {BASE_URL}")
        lines.append(f"\nOdjava od ove obavijesti: {BASE_URL}/api/alerts/unsubscribe/{alert['token']}")
        n = len(hits)
        if all(j.get("kind") == "praksa" for j in hits):
            subject = f"{n} " + plural(n, "nova praksa", "nove prakse", "novih praksi")
        else:
            subject = f"{n} " + plural(n, "novi oglas", "nova oglasa", "novih oglasa") + " za studente"
        try:
            send_alert(alert["email"], subject, "\n".join(lines))
            sent += 1
        except Exception as e:
            print(f"[alert] failed for alert {alert['id']}: {e}")
    return sent


def run_fetch(send_notifications: bool = True) -> dict:
    if not _fetch_lock.acquire(blocking=False):
        return {"skipped": "fetch already running"}
    try:
        results, status = fetch_all()
        new = []
        for source, jobs in results.items():
            new += db.sync_source(source, jobs, full_sync=FETCHERS[source][1])
        sent = notify_alerts(new) if send_notifications else 0
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
    kind: str | None = Query(None, pattern=KIND_PATTERN),
    remote: bool = False,
    source: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    total, items = db.search(q, city, category, kind, remote, source, limit=per_page, offset=(page - 1) * per_page)
    return {"total": total, "page": page, "per_page": per_page, "items": items}


@app.get("/api/meta")
def get_meta():
    return {
        **db.stats(),
        "cities": CITIES,
        "categories": CATEGORIES,
        "sources": [{"name": n, "url": SOURCE_LINKS[n]} for n in FETCHERS] + [{"name": db.MANUAL_SOURCE, "url": None}],
        "email_dev_mode": EMAIL_DEV_MODE,
    }


class NewInternship(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    company: str = Field(min_length=2, max_length=200)
    city: str | None = Field(None, max_length=100)
    location: str | None = Field(None, max_length=200)
    remote: bool = False
    kind: str = Field("praksa", pattern=KIND_PATTERN)
    category: str | None = None
    pay: str | None = Field(None, max_length=100)
    work_period: str | None = Field(None, max_length=100)
    url: HttpUrl | None = None
    description: str | None = Field(None, max_length=3000)
    contact_email: EmailStr


@app.post("/api/internships", status_code=201)
def post_internship(body: NewInternship):
    if body.city and body.city not in CITIES:
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
    kind: str | None = Field(None, pattern=KIND_PATTERN)
    remote_only: bool = False


@app.post("/api/alerts", status_code=201)
def create_alert(body: NewAlert):
    if not (body.keywords or body.city or body.category or body.kind or body.remote_only):
        raise HTTPException(400, "Zadaj barem jedan kriterij (ključne riječi, grad, područje ili vrstu).")
    if body.city and body.city not in CITIES:
        raise HTTPException(400, "Odaberi grad s popisa.")
    if body.category and body.category not in CATEGORIES:
        raise HTTPException(400, "Odaberi područje s popisa.")
    token = db.create_alert(body.email, (body.keywords or "").strip() or None, body.city, body.category,
                            body.kind, body.remote_only)
    summary = ", ".join(x for x in (
        body.keywords, body.city, body.category,
        {"praksa": "samo prakse", "studentski": "samo studentski poslovi"}.get(body.kind or ""),
        "rad od kuće" if body.remote_only else None) if x)
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
        return page("Obavijest potvrđena ✓", "Javit ćemo ti mailom čim se pojavi nova praksa koja odgovara tvojoj pretrazi.")
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
    rows = "".join(f"<tr><th style='text-align:left;padding-right:1em'>{escape(k)}</th><td>{escape(str(post.get(f) or '-'))}</td></tr>"
                   for k, f in (("Naziv", "title"), ("Tvrtka", "company"), ("Lokacija", "location"), ("Vrsta", "kind"),
                                ("Područje", "category"), ("Naknada", "pay"), ("Razdoblje", "work_period"),
                                ("Link", "url"), ("Kontakt", "contact_email"), ("Opis", "description")))
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
