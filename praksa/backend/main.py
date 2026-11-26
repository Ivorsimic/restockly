import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, HttpUrl

import db
from notifier import send_alert, DEV_MODE as EMAIL_DEV_MODE
from sources import fetch_all, FETCHERS

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8002").rstrip("/")
FETCH_INTERVAL_MINUTES = int(os.environ.get("FETCH_INTERVAL_MINUTES", "60"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

scheduler = BackgroundScheduler()
_fetch_lock = threading.Lock()


# ---------- matching + alerts ----------

def matches(alert: dict, job: dict) -> bool:
    haystack = " ".join(str(job.get(k) or "") for k in ("title", "company", "tags", "description")).lower()
    for word in (alert.get("keywords") or "").lower().split():
        if word not in haystack:
            return False
    if alert.get("location") and alert["location"].lower() not in (job.get("location") or "").lower():
        return False
    if alert.get("remote_only") and not job.get("remote"):
        return False
    if alert.get("kind") and alert["kind"] != job.get("kind"):
        return False
    return True


def notify_alerts(new_jobs: list[dict]) -> int:
    sent = 0
    for alert in db.active_alerts():
        hits = [j for j in new_jobs if matches(alert, j)]
        if not hits:
            continue
        lines = [f"Pronašli smo {len(hits)} novih oglasa koji odgovaraju tvojoj pretrazi:\n"]
        for j in hits[:25]:
            where = j.get("location") or ("Remote" if j.get("remote") else "")
            lines.append(f"• {j['title']} — {j.get('company') or '?'} ({where})\n  {j.get('url') or ('Prijave: ' + (j.get('contact_email') or ''))}")
        if len(hits) > 25:
            lines.append(f"\n…i još {len(hits) - 25}. Pogledaj sve na {BASE_URL}")
        lines.append(f"\nOdjava od ove obavijesti: {BASE_URL}/api/alerts/unsubscribe/{alert['token']}")
        try:
            send_alert(alert["email"], f"Nove prakse: {len(hits)} novih oglasa", "\n".join(lines))
            sent += 1
        except Exception as e:
            print(f"[alert] failed for alert {alert['id']}: {e}")
    return sent


def run_fetch(send_notifications: bool = True) -> dict:
    if not _fetch_lock.acquire(blocking=False):
        return {"skipped": "fetch already running"}
    try:
        jobs, status = fetch_all()
        new = db.upsert_internships(jobs)
        sent = notify_alerts(new) if send_notifications else 0
        status_str = "; ".join(f"{k}: {v}" for k, v in status.items())
        db.log_fetch(len(new), status_str)
        print(f"[fetch] {len(jobs)} matched, {len(new)} new, {sent} alert emails | {status_str}")
        return {"matched": len(jobs), "new": len(new), "alerts_sent": sent, "sources": status}
    finally:
        _fetch_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    first_run = db.stats()["last_fetch"] is None
    # Initial fetch in the background so the server starts instantly. Don't email on the very first
    # fill, otherwise every alert would match the whole backlog.
    threading.Thread(target=run_fetch, kwargs={"send_notifications": not first_run}, daemon=True).start()
    scheduler.add_job(run_fetch, "interval", minutes=FETCH_INTERVAL_MINUTES, id="fetch")
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Praksa — tražilica studentskih praksi", lifespan=lifespan)


# ---------- API ----------

@app.get("/api/internships")
def list_internships(
    q: str | None = None,
    location: str | None = None,
    remote: bool = False,
    kind: str | None = Query(None, pattern="^(praksa|studentski)$"),
    source: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    total, items = db.search(q, location, remote, kind, source, limit=per_page, offset=(page - 1) * per_page)
    return {"total": total, "page": page, "per_page": per_page, "items": items}


@app.get("/api/stats")
def get_stats():
    return {**db.stats(), "sources": list(FETCHERS) + ["Objavljeno ovdje"], "email_dev_mode": EMAIL_DEV_MODE}


class NewInternship(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    company: str = Field(min_length=2, max_length=200)
    location: str | None = Field(None, max_length=200)
    remote: bool = False
    kind: str = Field("praksa", pattern="^(praksa|studentski)$")
    tags: str | None = Field(None, max_length=300)
    url: HttpUrl | None = None
    description: str | None = Field(None, max_length=3000)
    contact_email: EmailStr


@app.post("/api/internships", status_code=201)
def post_internship(body: NewInternship):
    data = body.model_dump()
    data["url"] = str(body.url) if body.url else None
    new_id = db.add_manual_internship(data)
    notify_alerts([{**data, "id": new_id}])
    return {"id": new_id}


class NewAlert(BaseModel):
    email: EmailStr
    keywords: str | None = Field(None, max_length=200)
    location: str | None = Field(None, max_length=100)
    remote_only: bool = False
    kind: str | None = Field(None, pattern="^(praksa|studentski)$")


@app.post("/api/alerts", status_code=201)
def create_alert(body: NewAlert):
    if not (body.keywords or body.location or body.remote_only or body.kind):
        raise HTTPException(400, "Zadaj barem jedan kriterij (ključne riječi, lokaciju, remote ili vrstu).")
    token = db.create_alert(body.email, (body.keywords or "").strip() or None,
                            (body.location or "").strip() or None, body.remote_only, body.kind)
    send_alert(
        body.email,
        "Obavijest o praksama je uključena",
        f"Javit ćemo ti kad se pojave nove prakse za: {body.keywords or '-'} / {body.location or 'bilo gdje'}"
        f"{' / samo remote' if body.remote_only else ''}.\n\n"
        f"Odjava: {BASE_URL}/api/alerts/unsubscribe/{token}",
    )
    return {"ok": True, "email_dev_mode": EMAIL_DEV_MODE}


@app.get("/api/alerts/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(token: str):
    ok = db.deactivate_alert(token)
    msg = "Odjavljen/a si od ove obavijesti." if ok else "Obavijest ne postoji ili je već isključena."
    return f"<!doctype html><meta charset=utf-8><title>Odjava</title><body style='font-family:sans-serif;padding:2rem'><p>{msg}</p><p><a href='/'>Natrag na prakse</a></p>"


@app.post("/api/admin/fetch")
def trigger_fetch(x_admin_token: str | None = Header(None)):
    """Manually trigger a fetch. Requires ADMIN_TOKEN env var + matching X-Admin-Token header."""
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, "Forbidden")
    return run_fetch()


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
