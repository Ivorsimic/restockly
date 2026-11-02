import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

import db
from notifier import send_alert, with_affiliate_tag, DEV_MODE as EMAIL_DEV_MODE
from plans import PLANS, get_plan
from watcher import check_page

SCHEDULER_TICK_MINUTES = 1  # scheduler runs every minute; per-watch interval comes from the owner's plan

stripe = None
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
if STRIPE_SECRET_KEY:
    import stripe as _stripe
    stripe = _stripe
    stripe.api_key = STRIPE_SECRET_KEY
STRIPE_DEV_MODE = stripe is None
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")

scheduler = BackgroundScheduler()


def _due_for_check(watch) -> bool:
    plan = get_plan(watch["plan"])
    if not watch["last_checked_at"]:
        return True
    last = datetime.fromisoformat(watch["last_checked_at"])
    age_minutes = (datetime.now(timezone.utc) - last).total_seconds() / 60
    return age_minutes >= plan["check_interval_minutes"]


def run_checks():
    for watch in db.list_all_active_watches_with_plan():
        if not _due_for_check(watch):
            continue

        result = check_page(watch["url"])
        if not result.get("ok"):
            continue

        price = result.get("price")
        in_stock = result.get("in_stock")

        prev_price = watch["last_price"]
        prev_in_stock = watch["last_in_stock"]
        prev_in_stock = None if prev_in_stock is None else bool(prev_in_stock)

        notify_reasons = []
        if watch["target_price"] is not None and price is not None:
            if price <= watch["target_price"] and (prev_price is None or prev_price > watch["target_price"]):
                notify_reasons.append(f"Price dropped to {price} (target: {watch['target_price']})")
        if in_stock is True and prev_in_stock is False:
            notify_reasons.append("Item is back in stock!")

        if notify_reasons:
            send_alert(
                watch["email"],
                subject="WatchPage alert: your item changed",
                body=f"{with_affiliate_tag(watch['url'])}\n\n" + "\n".join(notify_reasons),
            )

        db.update_watch_state(watch["id"], price, in_stock)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler.add_job(run_checks, "interval", minutes=SCHEDULER_TICK_MINUTES, id="check_watches")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="WatchPage", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class CreateWatch(BaseModel):
    url: HttpUrl
    email: str
    target_price: float | None = None


@app.post("/api/watches")
def create_watch(payload: CreateWatch):
    plan = get_plan(db.get_user_plan(payload.email))
    if db.count_active_watches(payload.email) >= plan["watch_limit"]:
        raise HTTPException(
            402,
            f"Your {plan['label']} plan allows {plan['watch_limit']} watches. Upgrade for more + faster checks.",
        )

    result = check_page(str(payload.url))
    if not result.get("ok"):
        raise HTTPException(422, f"Couldn't read that page: {result.get('error')}")

    watch_id = db.create_watch(str(payload.url), payload.email, payload.target_price)
    db.update_watch_state(watch_id, result.get("price"), result.get("in_stock"))

    return {
        "id": watch_id,
        "detected_price": result.get("price"),
        "detected_in_stock": result.get("in_stock"),
        "detection_source": result.get("source"),
    }


@app.get("/api/watches")
def get_watches(email: str):
    rows = db.list_watches_for_email(email)
    return [dict(r) for r in rows]


@app.delete("/api/watches/{watch_id}")
def delete_watch(watch_id: int, email: str):
    if not db.deactivate_watch(watch_id, email):
        raise HTTPException(404, "Watch not found")
    return {"ok": True}


@app.post("/api/watches/{watch_id}/check-now")
def check_now(watch_id: int, email: str):
    rows = db.list_watches_for_email(email)
    watch = next((r for r in rows if r["id"] == watch_id), None)
    if not watch:
        raise HTTPException(404, "Watch not found")
    result = check_page(watch["url"])
    if result.get("ok"):
        db.update_watch_state(watch_id, result.get("price"), result.get("in_stock"))
    return result


@app.get("/api/plans")
def list_plans():
    return PLANS


@app.get("/api/me")
def me(email: str):
    plan_id = db.get_user_plan(email)
    return {"email": email, "plan": plan_id, **get_plan(plan_id)}


@app.post("/api/checkout/{plan_id}")
def checkout(plan_id: str, email: str):
    if plan_id not in PLANS or plan_id == "free":
        raise HTTPException(400, "Invalid plan.")
    plan = PLANS[plan_id]

    if STRIPE_DEV_MODE:
        return {
            "dev_mode": True,
            "message": f"Stripe not configured yet - granting {plan['label']} for free while testing.",
            "activate_url": f"/api/dev-activate/{plan_id}?email={email}",
        }

    checkout_session = stripe.checkout.Session.create(
        line_items=[{"price": plan["stripe_price_id"], "quantity": 1}],
        mode="subscription",
        success_url=f"{BASE_URL}/?email={email}&upgraded=1",
        cancel_url=f"{BASE_URL}/?email={email}&upgraded=0",
        customer_email=email,
        metadata={"email": email, "plan": plan_id},
    )
    return {"dev_mode": False, "checkout_url": checkout_session.url}


@app.post("/api/dev-activate/{plan_id}")
def dev_activate(plan_id: str, email: str):
    if not STRIPE_DEV_MODE:
        raise HTTPException(403, "Only available while Stripe is not configured.")
    if plan_id not in PLANS:
        raise HTTPException(400, "Invalid plan.")
    db.set_user_plan(email, plan_id)
    return {"ok": True, "plan": plan_id}


@app.post("/api/webhook/stripe")
async def stripe_webhook(payload: dict):
    # TODO: verify signature with stripe.Webhook.construct_event before production use
    if payload.get("type") == "checkout.session.completed":
        meta = payload["data"]["object"].get("metadata", {})
        if meta.get("email") and meta.get("plan"):
            db.set_user_plan(meta["email"], meta["plan"])
    return {"received": True}


@app.get("/api/status")
def status():
    return {"email_dev_mode": EMAIL_DEV_MODE, "stripe_dev_mode": STRIPE_DEV_MODE}


app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
