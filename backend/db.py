import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from plans import DEFAULT_PLAN

DB_PATH = "watchpage.db"


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                email TEXT NOT NULL,
                target_price REAL,
                last_price REAL,
                last_in_stock INTEGER,
                last_checked_at TEXT,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                plan TEXT NOT NULL DEFAULT 'free',
                stripe_customer_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_or_create_user(email: str) -> sqlite3.Row:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return row
        conn.execute(
            "INSERT INTO users (email, plan, created_at) VALUES (?, ?, ?)",
            (email, DEFAULT_PLAN, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def set_user_plan(email: str, plan: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (email, plan, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET plan = excluded.plan",
            (email, plan, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_user_plan(email: str) -> str:
    return get_or_create_user(email)["plan"]


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def create_watch(url: str, email: str, target_price: float | None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO watches (url, email, target_price, created_at) VALUES (?, ?, ?, ?)",
            (url, email, target_price, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid


def list_watches_for_email(email: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM watches WHERE email = ? AND active = 1 ORDER BY created_at DESC", (email,)
        ).fetchall()


def count_active_watches(email: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM watches WHERE email = ? AND active = 1", (email,)
        ).fetchone()
        return row["c"]


def list_all_active_watches_with_plan() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT w.*, COALESCE(u.plan, 'free') AS plan
            FROM watches w
            LEFT JOIN users u ON u.email = w.email
            WHERE w.active = 1
            """
        ).fetchall()


def update_watch_state(watch_id: int, price: float | None, in_stock: bool | None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE watches SET last_price = ?, last_in_stock = ?, last_checked_at = ? WHERE id = ?",
            (price, None if in_stock is None else int(in_stock), datetime.now(timezone.utc).isoformat(), watch_id),
        )
        conn.commit()


def deactivate_watch(watch_id: int, email: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE watches SET active = 0 WHERE id = ? AND email = ?", (watch_id, email)
        )
        conn.commit()
        return cur.rowcount > 0
